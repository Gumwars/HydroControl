import QtQuick
import qs.Common

QtObject {
    id: root

    function check(done) {
        Proc.runCommand("hydroc.startupCheck",
            ["curl", "-s", "--max-time", "3", "http://127.0.0.1:8781/api/health"],
            (stdout, exitCode) => {
                if (exitCode === 0 && stdout.indexOf('"ok"') >= 0) {
                    done(null);
                    return;
                }
                done({
                    title: "hydroc-server is not reachable",
                    details: "The HydroControl widget talks to hydroc-server over loopback HTTP.\n\n" +
                        "Start it with:\n" +
                        "  sudo systemctl start hydroc-server.service\n\n" +
                        "or run `dms ipc call plugins reload hydroc` after starting it."
                });
            });
    }
}
