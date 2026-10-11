"""Regelt eine Warmwasserheizung anhand der gemessenen KSEM-Netzleistung."""

import argparse
import sys
import time

from Frequenz import ImpulsAnsteuerung
from KSEM_Einspeisung import lese_netzleistung


TOTE_ZONE_WATT = 30.0
REGLER_VERSTAERKUNG = 0.5
MAX_AENDERUNG_PRO_ZYKLUS_PROZENT = 10.0


def begrenze(wert: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(wert, maximum))


def berechne_neuen_prozentwert(
    aktueller_prozentwert: float, netzleistung_watt: float, heizleistung_watt: float
) -> float:
    if abs(netzleistung_watt) <= TOTE_ZONE_WATT:
        return aktueller_prozentwert

    aenderung = (
        REGLER_VERSTAERKUNG * netzleistung_watt / heizleistung_watt * 100
    )
    aenderung = begrenze(
        aenderung,
        -MAX_AENDERUNG_PRO_ZYKLUS_PROZENT,
        MAX_AENDERUNG_PRO_ZYKLUS_PROZENT,
    )
    return begrenze(aktueller_prozentwert + aenderung, 0.0, 100.0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regelt die Heizleistung so, dass die KSEM-Netzleistung "
            "möglichst nahe bei null bleibt."
        )
    )
    parser.add_argument("ip", help="IP-Adresse des KSEM")
    parser.add_argument(
        "heizleistung",
        type=float,
        help="Nennleistung der Heizung in Watt (zum Beispiel 3300)",
    )
    parser.add_argument(
        "--port", type=int, default=502, help="Modbus-TCP-Port (Standard: 502)"
    )
    parser.add_argument(
        "--einheit-id",
        type=int,
        default=1,
        help="Modbus-Einheit-ID (Standard: 1)",
    )
    parser.add_argument(
        "--intervall",
        type=float,
        default=2.0,
        help="Regelintervall in Sekunden (Standard: 2)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Zeitlimit für die Modbus-Abfrage in Sekunden (Standard: 3)",
    )
    parser.add_argument(
        "--offset-watt",
        type=float,
        default=0.0,
        help=(
            "temporärer Offset in Watt für die Regelung "
            "(positiv simuliert zusätzliche Einspeisung; Standard: 0)"
        ),
    )
    argumente = parser.parse_args()

    if argumente.heizleistung <= 0:
        parser.error("Die Heizleistung muss größer als 0 W sein.")
    if not 1 <= argumente.port <= 65535:
        parser.error("--port muss zwischen 1 und 65535 liegen.")
    if not 0 <= argumente.einheit_id <= 255:
        parser.error("--einheit-id muss zwischen 0 und 255 liegen.")
    if argumente.intervall <= 0 or argumente.timeout <= 0:
        parser.error("--intervall und --timeout müssen größer als 0 sein.")

    prozent = 0.0
    try:
        with ImpulsAnsteuerung() as ansteuerung:
            ansteuerung.setze_prozent(0)
            print(
                f"KSEM: {argumente.ip}, Heizung maximal "
                f"{argumente.heizleistung:.0f} W. Regelung läuft; "
                "Abbruch mit Strg+C."
            )
            print(
                "Bei positiver Einspeisung wird die Heizung hochgeregelt, "
                "bei Netzbezug heruntergeregelt."
            )
            if argumente.offset_watt != 0:
                offset_text = f"{argumente.offset_watt:+.1f}".replace(".", ",")
                print(
                    f"ACHTUNG: Für die Regelung wird ein Offset von "
                    f"{offset_text} W verwendet. Ein positiver Offset kann "
                    "die Heizung auch ohne entsprechende PV-Einspeisung einschalten."
                )

            while True:
                gemessene_netzleistung = lese_netzleistung(
                    argumente.ip,
                    argumente.port,
                    argumente.einheit_id,
                    argumente.timeout,
                )
                netzleistung = gemessene_netzleistung + argumente.offset_watt

                prozent = berechne_neuen_prozentwert(
                    prozent, netzleistung, argumente.heizleistung
                )

                ganzzahl_prozent = round(prozent)
                ansteuerung.setze_prozent(ganzzahl_prozent)
                gemessen_text = f"{gemessene_netzleistung:+.1f}".replace(".", ",")
                leistung_text = f"{netzleistung:+.1f}".replace(".", ",")
                heizleistung_text = (
                    f"{argumente.heizleistung * ganzzahl_prozent / 100:.0f}"
                )
                sys.stdout.write(
                    f"\r\033[2KNetzleistung: {gemessen_text} W | "
                    f"Regelwert mit Offset: {leistung_text} W | "
                    f"Heizungsansteuerung: {ganzzahl_prozent} % "
                    f"({heizleistung_text} W von "
                    f"{argumente.heizleistung:.0f} W)"
                )
                sys.stdout.flush()
                time.sleep(argumente.intervall)
    except KeyboardInterrupt:
        print("\nRegelung beendet; GPIO wird ausgeschaltet.")
        return 0
    except (ConnectionError, OSError, RuntimeError) as fehler:
        print(
            f"Fehler: {fehler}\n"
            "Regelung wird beendet; GPIO wird ausgeschaltet.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
