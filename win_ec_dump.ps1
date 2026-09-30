<#
.SYNOPSIS
  Read-only EC range dump through \\.\ACPIDriver, for diffing two moments.

.DESCRIPTION
  READ-ONLY -- uses IOCTL_GPD_ACPI_ECREAD (0x9C40A488), the same read
  GCUService makes, and nothing else. Needs an elevated shell.

  Paced at 20 ms per access (double GCUService's 10 ms) so it can run beside
  win_battery_log.ps1 without the two together crowding the EC below the 6 ms
  spacing DESIGN.md 4.2 requires. The default ranges are 192 reads, about 4 s.

  The fan tacho registers 0x0464/5 and 0x046C/D are always skipped (reported
  as "--"), whatever range is asked for.

  Output: a hex table on the console, and a JSON file of addr -> value that
  can be diffed against a later dump.

.EXAMPLE
  .\win_ec_dump.ps1
  .\win_ec_dump.ps1 -Ranges 0x0500-0x057F -Out dump-a.json
#>
param(
    [string[]]$Ranges = @('0x0300-0x033F', '0x0500-0x057F'),
    [string]$Out = ""
)
$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Needs an elevated shell (\\.\ACPIDriver)." }

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class UwEcDump
{
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern SafeFileHandle CreateFile(string n, uint a, uint s, IntPtr sec, uint d, uint f, IntPtr t);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool DeviceIoControl(SafeFileHandle h, uint code, ref int inBuf, int inSize,
        out int outBuf, int outSize, out int ret, IntPtr ov);
    static SafeFileHandle h;
    public static bool Open() { h = CreateFile(@"\\.\ACPIDriver", 0xC0000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero); return !h.IsInvalid; }
    public static void Close() { if (h != null) h.Dispose(); }
    public static int Read(int addr)
    {
        int a = addr, o = 0, n = 0;
        bool ok = DeviceIoControl(h, 0x9C40A488, ref a, 4, out o, 4, out n, IntPtr.Zero);
        System.Threading.Thread.Sleep(20);
        return ok ? (o & 0xFF) : -1;
    }
}
'@

$never = @(0x0464, 0x0465, 0x046C, 0x046D)
if (-not [UwEcDump]::Open()) { throw "\\.\ACPIDriver would not open." }
$data = [ordered]@{}
try {
    # With -File, "a-b,c-d" arrives as one string; accept both forms.
    foreach ($r in ($Ranges | ForEach-Object { $_ -split ',' } | Where-Object { $_ })) {
        $lo, $hi = $r -split '-' | ForEach-Object { [Convert]::ToInt32($_, 16) }
        "== 0x{0:X4}-0x{1:X4}" -f $lo, $hi
        for ($row = $lo - ($lo % 16); $row -le $hi; $row += 16) {
            $cells = for ($c = 0; $c -lt 16; $c++) {
                $a = $row + $c
                if ($a -lt $lo -or $a -gt $hi) { '  ' }
                elseif ($never -contains $a) { '--' }
                else { $v = [UwEcDump]::Read($a); $data['0x{0:X4}' -f $a] = $v; if ($v -lt 0) { '??' } else { '{0:X2}' -f $v } }
            }
            '{0:X4}: {1}' -f $row, ($cells -join ' ')
        }
    }
} finally { [UwEcDump]::Close() }

if (-not $Out) { $Out = Join-Path $PSScriptRoot ("ec-dump-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss')) }
[pscustomobject]@{ when = (Get-Date).ToString('s'); values = $data } | ConvertTo-Json -Depth 3 | Set-Content -Encoding utf8 $Out
"saved $Out"
