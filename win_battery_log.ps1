<#
.SYNOPSIS
  Test 1 logger for WINDOWS-CAPTURE.md: battery telemetry plus the charge
  registers, every N seconds, one CSV row per sample.

.DESCRIPTION
  READ-ONLY. Never writes to the EC, NVRAM or Control Center settings.

  Battery state comes from WMI (root\wmi BatteryStatus) and needs no rights.
  EC registers are read the same way GCUService does it -- DeviceIoControl
  IOCTL 0x9C40A488 (IOCTL_GPD_ACPI_ECREAD) on \\.\ACPIDriver, a 4-byte address
  in, the low byte of a 4-byte reply out -- which needs an elevated shell.
  Without elevation the EC columns are left empty and WMI logging still runs.

  Pacing: 10 ms between EC accesses, as GCUService does (DESIGN.md 4.2). About
  35 reads per sample, ~0.4 s -- at -Interval 5 that is under a tenth of the
  time, with long idle gaps between bursts. The fan tacho registers (0x0464/5, 0x046C/D) are never
  read.

  Every row is appended and flushed immediately, so a crash or power-off loses
  at most the sample in flight.

.EXAMPLE
  .\win_battery_log.ps1                 # 30 s interval, CSV in the current dir
  .\win_battery_log.ps1 -Interval 10    # test 3 cadence
  .\win_battery_log.ps1 -Once           # one snapshot to the console, no CSV
#>
param(
    [int]$Interval = 30,
    [string]$Out = "",
    [switch]$Once
)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class UwEc
{
    const uint IOCTL_GPD_ACPI_ECREAD = 0x9C40A488;

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share,
        IntPtr sec, uint disposition, uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool DeviceIoControl(SafeFileHandle h, uint code,
        ref int inBuf, int inSize, out int outBuf, int outSize,
        out int returned, IntPtr overlapped);

    static SafeFileHandle handle;

    public static bool Open()
    {
        // GENERIC_READ|GENERIC_WRITE, FILE_SHARE_READ|WRITE, OPEN_EXISTING --
        // the exact arguments GCUService passes.
        handle = CreateFile(@"\\.\ACPIDriver", 0xC0000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
        return !handle.IsInvalid;
    }

    public static void Close() { if (handle != null) handle.Dispose(); }

    // -1 on failure, so a failed read can never be mistaken for a value.
    public static int Read(int addr)
    {
        int a = addr, outv = 0, n = 0;
        bool ok = DeviceIoControl(handle, IOCTL_GPD_ACPI_ECREAD, ref a, 4, out outv, 4, out n, IntPtr.Zero);
        System.Threading.Thread.Sleep(10);
        return ok ? (outv & 0xFF) : -1;
    }
}
'@

# Registers to watch, with the Linux value to diff against (WINDOWS-CAPTURE.md).
$Regs = [ordered]@{
    'r07A6' = 0x07A6   # charge profile, bits 5:4  (Linux 0x20 / 0x00)
    'r07B9' = 0x07B9   # CHARGE_LIMIT_UP, b7 REACHED  (Linux 0x50)
    'r07D0' = 0x07D0   # CHARGE_LIMIT_DOWN -- never read before
    'r07C3' = 0x07C3   # percentage-ceiling gate, arms at 4  (Linux 0x0D)
    'r0742' = 0x0742   # b2 = ceiling footprint  (Linux 0)
    'r07C6' = 0x07C6   # AP_OEM_6, ERM / FULL_OVER_24H bits  (Linux 0x04)
    'r07CC' = 0x07CC   # USB-C adapter priority, b7
    'r0491' = 0x0491   # cell-count selector  (Linux 0xC0 = 4)
    'r04AB' = 0x04AB   # capacity percent (sanity)
    'r0571' = 0x0571   # EC charge state: 2 charging, 0 idle/full, 1 discharging
    'r0566' = 0x0566   # unknown, moves with charge state (248 / 52 / 55)
}
# Multi-byte values: name, low address, high address, big-endian?, [signed?]
# (Leading commas keep PowerShell from flattening the rows into one list.)
$Words = @(
    ,@('chg_target_mV', 0x0522, 0x0523, $false)   # LE, Linux 16800
    ,@('pack_max_mV',   0x030E, 0x030F, $true)    # BE, Linux 17800
    ,@('bat_temp_dK',   0x0502, 0x0503, $false)   # 0.1 K, Linux 3030
    ,@('bat_temp2_dK',  0x04A2, 0x04A3, $false)   # copy feeding the stress accumulator
    ,@('cycles',        0x04A6, 0x04A7, $false)   # Linux 133
    ,@('ec_rate',       0x0434, 0x0435, $false)   # _BST present rate, mA -- the current to trust
    ,@('ec_remaining',  0x0436, 0x0437, $false)   # _BST remaining capacity
    ,@('ec_voltage',    0x0438, 0x0439, $false)   # _BST present voltage
    ,@('ec_live_mA',    0x050A, 0x050B, $false, $true)   # gauge current, signed (negative = discharge); copies to 0x050F
    ,@('ec_avg_mA',     0x0510, 0x0511, $false, $true)   # fourth copy, differs slightly -- probably average current
    ,@('ec_v2_mV',      0x0506, 0x0507, $false)   # gauge voltage, copy at 0x0508
    ,@('gauge_pct',     0x0514, 0x0515, $false)   # gauge relative state of charge, copy at 0x0516
    ,@('gauge_rem_mAh', 0x0518, 0x0519, $false)   # gauge remaining capacity, copy at 0x051A
    ,@('gauge_fcc_mAh', 0x051C, 0x051D, $false)   # gauge full-charge capacity -- changes if the gauge relearns
    ,@('r0542_word',    0x0542, 0x0543, $false)   # unknown; rises while discharging, so not live voltage
)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
$ecOk = $false
if ($isAdmin) {
    $ecOk = [UwEc]::Open()
    if (-not $ecOk) { Write-Warning "\\.\ACPIDriver would not open -- EC columns will be empty." }
} else {
    Write-Warning "Not elevated: logging battery telemetry only, no EC registers."
}

function Read-Sample {
    $b  = Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus | Select-Object -First 1
    $fc = (Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity | Select-Object -First 1).FullChargedCapacity
    $pct = (Get-CimInstance Win32_Battery | Select-Object -First 1).EstimatedChargeRemaining

    # WMI rates are mW and capacities mWh. current_mA is derived as rate /
    # voltage, but Windows caches the rate (often pinned at ec_rate x the 15.48 V
    # design voltage), so it drifts with voltage and lags. Use ec_rate instead;
    # current_mA is kept only so the CSV columns stay comparable across runs.
    $rate = if ($b.Charging) { [int]$b.ChargeRate } elseif ($b.Discharging) { -[int]$b.DischargeRate } else { 0 }
    $mA = if ($b.Voltage -gt 0) { [math]::Round($rate * 1000.0 / $b.Voltage) } else { '' }

    $row = [ordered]@{
        time         = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
        ac           = [int][bool]$b.PowerOnline
        charging     = [int][bool]$b.Charging
        discharging  = [int][bool]$b.Discharging
        pct_os       = $pct
        remaining_mWh = $b.RemainingCapacity
        full_mWh     = $fc
        pct_calc     = if ($fc) { [math]::Round(100.0 * $b.RemainingCapacity / $fc, 2) } else { '' }
        voltage_mV   = $b.Voltage
        v_per_cell   = if ($b.Voltage) { [math]::Round($b.Voltage / 4000.0, 4) } else { '' }
        rate_mW      = $rate
        current_mA   = $mA
    }

    foreach ($k in $Regs.Keys) {
        $row[$k] = if ($ecOk) { $v = [UwEc]::Read($Regs[$k]); if ($v -ge 0) { '0x{0:X2}' -f $v } else { 'ERR' } } else { '' }
    }
    foreach ($w in $Words) {
        if ($ecOk) {
            $lo = [UwEc]::Read($w[1]); $hi = [UwEc]::Read($w[2])
            $v = if ($lo -lt 0 -or $hi -lt 0) { 'ERR' }
                 elseif ($w[3]) { ($lo -shl 8) -bor $hi }
                 else { ($hi -shl 8) -bor $lo }
            if ($w.Count -gt 4 -and $w[4] -and $v -is [int] -and $v -ge 0x8000) { $v -= 0x10000 }
            $row[$w[0]] = $v
        } else { $row[$w[0]] = '' }
    }
    $row['bat_temp_C'] = if ($row.bat_temp_dK -is [int] -and $row.bat_temp_dK -gt 0) { [math]::Round($row.bat_temp_dK / 10.0 - 273.15, 2) } else { '' }
    [pscustomobject]$row
}

try {
    if ($Once) {
        Read-Sample | Format-List
        return
    }

    if (-not $Out) { $Out = Join-Path (Get-Location) ("win-test1-{0}.csv" -f (Get-Date -Format 'yyyyMMdd-HHmmss')) }
    Write-Host "Logging every $Interval s to $Out  (Ctrl+C to stop)"
    $first = $true
    $lastProfile = $null
    while ($true) {
        $s = Read-Sample
        $line = $s | ConvertTo-Csv -NoTypeInformation
        # ConvertTo-Csv emits header + row; keep the header only once.
        if ($first) { [IO.File]::AppendAllText($Out, $line[0] + "`r`n"); $first = $false }
        [IO.File]::AppendAllText($Out, $line[1] + "`r`n")

        $flag = ''
        if ($lastProfile -ne $null -and $s.r07A6 -ne $lastProfile) { $flag = "  <-- 0x07A6 changed from $lastProfile" }
        $lastProfile = $s.r07A6
        Write-Host ("{0}  ac={1} gauge {2,3}% {3,4}/{4} mAh  EC {5,6} mV {6,6} mA  st={7} target={8} 07A6={9} 07C3={10} T={11}C{12}" -f `
            $s.time, $s.ac, $s.gauge_pct, $s.gauge_rem_mAh, $s.gauge_fcc_mAh, $s.ec_voltage, $s.ec_live_mA,
            $s.r0571, $s.chg_target_mV, $s.r07A6, $s.r07C3, $s.bat_temp_C, $flag)
        Start-Sleep -Seconds $Interval
    }
} finally {
    if ($ecOk) { [UwEc]::Close() }
}
