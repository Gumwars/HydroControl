import QtQuick
import qs.Common

QtObject {
    id: root

    function check(done) {
        const py = "/usr/bin/python3";
        const pypath = "/home/gumwars/kbctrl";
        Proc.runCommand("kbctrl.startupCheck",
            ["/usr/bin/env", "PYTHONPATH=" + pypath, py, "-m", "kbctrl.ctl", "status"],
            (stdout, exitCode) => {
                if (exitCode === 0) {
                    done(null);
                    return;
                }
                done({
                    title: "kbctrld is not reachable",
                    details: "The kbctrl plugin talks to the kbctrld daemon over its Unix socket.\n\n" +
                        "Start it with:\n" +
                        "  sudo systemctl start kbctrld.service\n\n" +
                        "or run `dms ipc call plugins reload kbctrl` after starting it."
                });
            });
    }
}