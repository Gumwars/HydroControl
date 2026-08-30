# HydroControl persistence units

The EC forgets the charging profile, the custom-profile latch and the CPU power
limits on every power cycle. These units re-apply them.

## Install

    sudo cp hydroc-apply.service hydroc-resume.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now hydroc-apply.service
    sudo systemctl enable hydroc-resume.service

## Profile

Settings live in `/etc/hydroc/profile.json`. Create it from the defaults:

    sudo python3 -m hydroc.cli write-default-profile

Then edit. `apply` only writes settings that differ from actual hardware state,
and verifies every EC write by reading it back.

## Check

    python3 -m hydroc.cli drift      # exit 1 if hardware disagrees with profile
    systemctl status hydroc-apply

## Notes

- `PYTHONPATH` points at the working tree. Change it if you install the package
  properly.
- `apply` is idempotent; running it repeatedly is safe.
- The resume unit is insurance. Suspend usually keeps the EC powered, but the
  uniwill driver restores several registers on resume, which suggests some
  state is lost.
