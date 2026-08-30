#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# HydroControl installer — Eluktronics HYDROC-16 G1 only.
#
# Installs: the patched uniwill-laptop kernel module, boot-time module loading,
# and the systemd units that re-apply EC settings after every power cycle.
#
#   ./install.sh            full install
#   ./install.sh --check    verify prerequisites, change nothing
#   ./install.sh --module   rebuild + reinstall the kernel module only
#                           (with DKMS this is rarely needed — it rebuilds itself)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL="$(uname -r)"
MODE="${1:-full}"

c_ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
c_bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
c_warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
step()   { printf '\n\033[1m%s\033[0m\n' "$1"; }
die()    { printf '\n\033[31mAborted:\033[0m %s\n' "$1" >&2; exit 1; }

# ── 1. Right machine? ────────────────────────────────────────────────────────
# EC register layouts differ between chassis. Writing HYDROC-16 addresses on a
# different board could set anything at all, so this is a hard stop.
step "Checking hardware"
VENDOR="$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || echo unknown)"
BOARD="$(cat /sys/class/dmi/id/board_name 2>/dev/null || echo unknown)"
if [[ "$VENDOR" == "ELUKTRONICS" && "$BOARD" == HYDROC-16* ]]; then
  c_ok "$VENDOR / $BOARD"
else
  c_bad "$VENDOR / $BOARD"
  die "HydroControl is only validated on the Eluktronics HYDROC-16 G1.
       Its EC register map is specific to this chassis — running it elsewhere
       could write unknown values to your embedded controller. Not proceeding."
fi

# ── 2. Prerequisites ─────────────────────────────────────────────────────────
step "Checking prerequisites"
MISSING=()
[[ -d "/lib/modules/$KERNEL/build" ]] && c_ok "kernel headers for $KERNEL" \
  || { c_bad "kernel headers for $KERNEL"; MISSING+=("linux-headers (matching your kernel)"); }
command -v gcc >/dev/null && c_ok "gcc" || { c_bad "gcc"; MISSING+=("base-devel"); }
command -v make >/dev/null && c_ok "make" || { c_bad "make"; MISSING+=("base-devel"); }
python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null \
  && c_ok "python $(python3 -V 2>&1 | awk '{print $2}')" \
  || { c_bad "python >= 3.10"; MISSING+=("python"); }

if modinfo acpi_call >/dev/null 2>&1; then c_ok "acpi_call module available"
else c_bad "acpi_call not installed"; MISSING+=("acpi_call-dkms (AUR)"); fi

# Every dependency question goes through hydroc.deps, which probes in a
# subprocess reproducing the daemon's environment (root, HOME=/root, no
# PYTHONPATH). Asking it here rather than importing in this shell is what stops
# the installer drifting away from what the daemon can actually load -- the
# trap in DESIGN.md 4.4, which reached a tester through this file.
# Returns 0 present, 1 missing, 2 unknown (we are not root, so we cannot know).
dep_detail=""
dep_rc=0
dep_check() {
  if dep_detail="$(PYTHONPATH="$DIR" python3 -m hydroc.deps check "$1" 2>&1)"; then
    dep_rc=0
  else
    dep_rc=$?
  fi
}

# RGB_USER is who must own the install -- under sudo, pipx installs into root's
# home where the daemon will still find it, but the user running `pipx list`
# later would not see it.
RGB_USER="${SUDO_USER:-$USER}"
dep_check ite8291r3-ctl
case $dep_rc in
  0) c_ok "ite8291r3-ctl (keyboard RGB) $dep_detail" ;;
  2) c_warn "ite8291r3-ctl (keyboard RGB) -- not checked; re-run with sudo" ;;
  *) c_bad "ite8291r3-ctl not found (keyboard RGB will be unavailable)"
     MISSING+=("ite8291r3-ctl  --  run as $RGB_USER, not root:  pipx install ite8291r3-ctl") ;;
esac

if ! command -v pipx >/dev/null; then
  c_warn "pipx not installed -- needed to install ite8291r3-ctl"
fi

# bleak drives the LPP cooling dock. Optional on purpose: most owners do not
# have a dock, and it should not block an install for a machine that will
# never use one. The sidecar simply will not start without it.
dep_check bleak
case $dep_rc in
  0) c_ok "bleak (LPP cooling dock) $dep_detail"; HAVE_BLEAK=1 ;;
  2) c_warn "bleak (LPP cooling dock) -- not checked; re-run with sudo"
     HAVE_BLEAK=0 ;;
  *) c_warn "bleak not importable by root -- LPP cooling dock control unavailable"
     while IFS= read -r _l; do c_warn "  $_l"; done <<< "$dep_detail"
     c_warn "  Then re-run this installer -- hydroc-lpp.service is skipped below."
     HAVE_BLEAK=0 ;;
esac

if ((${#MISSING[@]})); then
  printf '\nInstall these first:\n'
  printf '  %s\n' "${MISSING[@]}"
  printf '\n  paru -S acpi_call-dkms base-devel python pipx linux-cachyos-headers\n'
  printf '  pipx install ite8291r3-ctl        # as %s, not root\n' "$RGB_USER"
  die "missing prerequisites"
fi

if [[ "$MODE" == "--check" ]]; then
  step "Check only — nothing changed."
  exit 0
fi

[[ $EUID -eq 0 ]] || die "run with sudo (it installs a kernel module and systemd units)"

# ── 3. Kernel module ─────────────────────────────────────────────────────────
# DKMS is strongly preferred: without it the module must be rebuilt by hand
# after every kernel update, and the failure is silent — it presents as
# "hwmon is down", not "your module was rejected on vermagic".
DKMS_NAME="uniwill-laptop"
DKMS_VER="1.0-hydroc16"
DKMS_SRC="/usr/src/${DKMS_NAME}-${DKMS_VER}"

purge_manual_installs() {
  # Manually-installed copies live in updates/ and shadow DKMS's updates/dkms/.
  # A stale one from an older kernel is what makes modprobe fail on vermagic
  # instead of falling through to a working module.
  local found=0
  for old in /lib/modules/*/updates/uniwill-laptop.ko*; do
    [[ -e "$old" ]] || continue
    rm -f "$old"; c_warn "removed hand-installed module: $old"; found=1
  done
  (( found )) && depmod -a || true
}

if command -v dkms >/dev/null; then
  step "Installing kernel module via DKMS"

  # Drop any previously registered version (including this one, for reinstalls).
  while read -r line; do
    [[ -z "$line" ]] && continue
    ver="${line#*/}"; ver="${ver%%,*}"
    dkms remove -m "$DKMS_NAME" -v "$ver" --all >/dev/null 2>&1 \
      && c_warn "removed previous DKMS registration $DKMS_NAME/$ver"
  done < <(dkms status -m "$DKMS_NAME" 2>/dev/null | cut -d, -f1 | sort -u)

  purge_manual_installs

  rm -rf "$DKMS_SRC"; mkdir -p "$DKMS_SRC"
  cp "$DIR/uniwill-laptop/"{uniwill-acpi.c,uniwill-wmi.c,uniwill-wmi.h,Makefile,dkms.conf} "$DKMS_SRC/"
  c_ok "sources staged in $DKMS_SRC"

  dkms add -m "$DKMS_NAME" -v "$DKMS_VER" >/dev/null 2>&1 || true
  if dkms build -m "$DKMS_NAME" -v "$DKMS_VER" >/dev/null 2>&1; then
    c_ok "built for $KERNEL"
  else
    dkms build -m "$DKMS_NAME" -v "$DKMS_VER" 2>&1 | tail -15
    die "DKMS build failed — output above"
  fi
  dkms install -m "$DKMS_NAME" -v "$DKMS_VER" --force >/dev/null 2>&1 \
    || die "DKMS install failed"
  c_ok "installed — will rebuild automatically on every kernel update"

else
  c_warn "dkms not found — falling back to a manual build"
  c_warn "the module will need rebuilding after each kernel update:"
  c_warn "  sudo ./install.sh --module"
  step "Building uniwill-laptop for $KERNEL"
  cd "$DIR/uniwill-laptop"
  make clean >/dev/null 2>&1 || true
  make >/dev/null || die "module build failed — run 'make' in uniwill-laptop/ to see why"
  BUILT="$(modinfo ./uniwill-laptop.ko | awk '/^vermagic/{print $2}')"
  [[ "$BUILT" == "$KERNEL" ]] || die "built for $BUILT but running $KERNEL"
  c_ok "built, vermagic $BUILT"
  purge_manual_installs
  make -C "/lib/modules/$KERNEL/build" M="$PWD" modules_install >/dev/null
  depmod -a
  c_ok "installed to /lib/modules/$KERNEL/updates/"
  cd "$DIR"
fi

# Never unload without a file to load back -- see hydroc/kmod.py. A kernel
# upgrade since boot removes the running kernel's module tree, and the unload
# would succeed where the reload could not.
if ! KMOD_WHY="$(PYTHONPATH="$DIR" python3 -m hydroc.kmod check uniwill-laptop 2>&1)"; then
  die "$KMOD_WHY
       Reboot onto the current kernel, then re-run this installer."
fi
modprobe -r uniwill-laptop 2>/dev/null || true
modprobe uniwill-laptop || die "module installed but would not load — see: dmesg | tail"
LOADED_FROM="$(modinfo -n uniwill-laptop 2>/dev/null || echo '?')"
c_ok "loaded from $LOADED_FROM"

echo uniwill-laptop > /etc/modules-load.d/uniwill.conf
echo acpi_call     > /etc/modules-load.d/acpi_call.conf
c_ok "will load at boot"

[[ "$MODE" == "--module" ]] && { step "Module only — done."; exit 0; }

# ── 4. systemd units ─────────────────────────────────────────────────────────
step "Installing systemd units"
UNITS=(hydroc-apply hydroc-resume hydroc-server)
(( HAVE_BLEAK )) && UNITS+=(hydroc-lpp)
(( HAVE_BLEAK )) || c_warn "hydroc-lpp.service skipped -- bleak not importable by root"
for u in "${UNITS[@]}"; do
  sed "s|@INSTALL_DIR@|$DIR|g" "$DIR/hydroc/systemd/$u.service" > "/etc/systemd/system/$u.service"
  c_ok "$u.service"
done
systemctl daemon-reload
systemctl enable hydroc-apply.service  >/dev/null 2>&1 || true
systemctl enable hydroc-resume.service >/dev/null 2>&1 || true
c_ok "enabled — EC settings will be re-applied at boot and after resume"

# hydroc-server is installed but NOT enabled: the daemon has no job when nobody
# is looking at the UI. The unit exists so the desktop app can start it through
# pkexec with journal logging and a clean shutdown.
c_warn "hydroc-server installed but NOT enabled (the desktop app starts it on demand)"

# ── 4b. desktop application ──────────────────────────────────────────────────
step "Installing the desktop application"
if python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("WebKit","6.0")' 2>/dev/null; then
  cat > /usr/local/bin/hydrocontrol <<LAUNCHER
#!/bin/sh
# HydroControl desktop app. The UI is served by the daemon; this is the window.
export PYTHONPATH="$DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m hydroc.desktop "\$@"
LAUNCHER
  chmod 0755 /usr/local/bin/hydrocontrol
  install -Dm0644 "$DIR/hydroc/desktop_files/hydrocontrol.desktop" \
    /usr/share/applications/hydrocontrol.desktop
  command -v update-desktop-database >/dev/null && \
    update-desktop-database /usr/share/applications 2>/dev/null || true
  c_ok "hydrocontrol launcher + application menu entry"
else
  c_warn "GTK4 / WebKitGTK 6.0 not available -- desktop app not installed"
  c_warn "  install them with:  paru -S gtk4 webkitgtk-6.0 python-gobject"
fi

if (( HAVE_BLEAK )); then
  # Not enabled automatically: it holds a BLE connection, and on a machine
  # with no dock that is a scan loop nobody asked for. Owners opt in.
  c_warn "hydroc-lpp installed but NOT enabled. If you have the LPP dock:"
  c_warn "  sudo systemctl enable --now hydroc-lpp"
  c_warn "  (stop LibreLPP first if you use it: systemctl --user disable --now lpp-daemon)"
fi

# ── 5. Done ──────────────────────────────────────────────────────────────────
# doctor is the real verdict: it checks the things this script cannot, like
# whether the module actually bound and whether both RGB devices answer.
step "Verifying"
cd "$DIR"
DOCTOR_OK=0
python3 -m hydroc.cli doctor || DOCTOR_OK=1

cat <<EOF

$(printf '\033[1mNext:\033[0m')

  cd $DIR
  sudo python3 -m hydroc.server

then open  http://127.0.0.1:8781

To check the chin bar effects on your machine (and report back):

  sudo python3 lb_mode_probe.py

EOF

if (( DOCTOR_OK )); then
  c_warn "install finished, but doctor reported failures -- see above."
  c_warn "the app will start; the features behind those checks will not work."
  exit 1
fi
c_ok "all checks passed"
