"""Verteilt 15-ms-Steuerimpulse für einen Nulldurchgangs-Schalter.

Raspberry Pi 3B+: BCM-GPIO 17 (physischer Header-Pin 11).
Benötigt den pigpio-Dienst und das Python-Paket. Der Ausgang liefert nur ein
3,3-V-Logiksignal und darf ausschließlich eine dafür geeignete, isolierte
Schnittstelle ansteuern.

Bei 50 Hz dauert eine Halbwelle nominell 10 ms. Ein 15-ms-Impuls kann daher
je nach Phasenlage eine oder zwei Nulldurchgänge umfassen. Ohne ein Netz-
Nulldurchgangssignal am Pi ist die Impulszahl keine garantierte Anzahl
geschalteter Halbwellen und kein genaues Leistungsmaß. Zuerst nur das
Niedervolt-Steuersignal prüfen und geeignete isolierte Messgeräte verwenden.
"""

import argparse
import sys
import time

import pigpio


GPIO_PIN = 17
HALBWELLEN_DAUER_US = 10_000
IMPULS_DAUER_US = 15_000
HALBWELLEN_PRO_SEKUNDE = 100
WELLENFORM_SCHRITT_US = 5_000


def verteile_zeitfenster(prozent: int) -> list[bool]:
    """Liefert 100 gleichmäßig verteilte Zeitfenster mit Impulsen."""
    slots: list[bool] = []
    accumulator = 0
    for _ in range(HALBWELLEN_PRO_SEKUNDE):
        accumulator += prozent
        active = accumulator >= 100
        if active:
            accumulator -= 100
        slots.append(active)
    return slots


def erstelle_wellenform(prozent: int) -> list[pigpio.pulse]:
    """Erzeugt ein sich wiederholendes Sekundenmuster mit 15-ms-Impulsen."""
    high_mask = 1 << GPIO_PIN
    pulses: list[pigpio.pulse] = []
    slots = verteile_zeitfenster(prozent)
    schrittanzahl = 1_000_000 // WELLENFORM_SCHRITT_US

    for schritt in range(schrittanzahl):
        zeitpunkt_us = schritt * WELLENFORM_SCHRITT_US + WELLENFORM_SCHRITT_US // 2
        active = any(
            slots[slot]
            and (
                zeitpunkt_us - slot * HALBWELLEN_DAUER_US
            ) % 1_000_000 < IMPULS_DAUER_US
            for slot in range(HALBWELLEN_PRO_SEKUNDE)
        )
        pulses.append(
            pigpio.pulse(
                high_mask if active else 0,
                0 if active else high_mask,
                WELLENFORM_SCHRITT_US,
            )
        )

    return pulses


class ImpulsAnsteuerung:
    """Steuert GPIO 17 mit einem wiederholten Impulsmuster."""

    def __init__(self) -> None:
        self.pi = pigpio.pi()
        if not self.pi.connected:
            self.pi.stop()
            raise ConnectionError(
                "Verbindung zu pigpio fehlgeschlagen. Dienst mit "
                "`sudo pigpiod` starten."
            )

        self.wellenform_id: int | None = None
        self.prozent: int | None = None
        try:
            ergebnis = self.pi.set_mode(GPIO_PIN, pigpio.OUTPUT)
            if ergebnis < 0:
                raise RuntimeError(
                    f"BCM-GPIO {GPIO_PIN} konnte nicht als Ausgang "
                    f"eingerichtet werden ({ergebnis})."
                )
            self._pruefe(self.pi.write(GPIO_PIN, 0), "GPIO auf LOW setzen")
        except Exception:
            self.pi.stop()
            raise

    @staticmethod
    def _pruefe(ergebnis: int, aktion: str) -> None:
        if ergebnis < 0:
            raise RuntimeError(f"pigpio konnte {aktion} nicht ({ergebnis}).")

    def setze_prozent(self, prozent: int) -> None:
        if not 0 <= prozent <= 100:
            raise ValueError("Der Prozentwert muss zwischen 0 und 100 liegen.")
        if prozent == self.prozent:
            return

        self.pi.wave_tx_stop()
        self._pruefe(self.pi.write(GPIO_PIN, 0), "GPIO auf LOW setzen")
        if self.wellenform_id is not None:
            self.pi.wave_delete(self.wellenform_id)
            self.wellenform_id = None

        if prozent == 0:
            self.prozent = 0
            return

        ergebnis = self.pi.wave_add_generic(erstelle_wellenform(prozent))
        self._pruefe(ergebnis, "Impulse zum Wellenformmuster hinzufügen")
        wellenform_id = self.pi.wave_create()
        if wellenform_id < 0:
            raise RuntimeError(
                f"pigpio konnte das Wellenformmuster nicht erstellen "
                f"(Fehlercode {wellenform_id})."
            )

        ergebnis = self.pi.wave_send_repeat(wellenform_id)
        if ergebnis < 0:
            self.pi.wave_delete(wellenform_id)
            raise RuntimeError(
                f"pigpio konnte das Wellenformmuster nicht starten ({ergebnis})."
            )
        self.wellenform_id = wellenform_id
        self.prozent = prozent

    def schliessen(self) -> None:
        try:
            self.pi.wave_tx_stop()
            self.pi.write(GPIO_PIN, 0)
            if self.wellenform_id is not None:
                self.pi.wave_delete(self.wellenform_id)
                self.wellenform_id = None
        finally:
            self.pi.stop()

    def __enter__(self) -> "ImpulsAnsteuerung":
        return self

    def __exit__(self, *_: object) -> None:
        self.schliessen()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Erzeugt gleichmäßig verteilte 15-ms-Steuerimpulse."
    )
    parser.add_argument(
        "prozent",
        type=int,
        choices=range(101),
        metavar="0-100",
        help="gewünschte Anzahl ausgewählter Zeitfenster (1%% = 1 je Sekunde)",
    )
    prozent = parser.parse_args().prozent

    pi = pigpio.pi()
    if not pi.connected:
        print(
            "Verbindung zu pigpio fehlgeschlagen. Dienst mit `sudo pigpiod` starten.",
            file=sys.stderr,
        )
        pi.stop()
        return 1

    wave_id = None
    try:
        result = pi.set_mode(GPIO_PIN, pigpio.OUTPUT)
        if result < 0:
            raise RuntimeError(
                f"BCM-GPIO {GPIO_PIN} konnte nicht als Ausgang eingerichtet "
                f"werden ({result})."
            )
        result = pi.write(GPIO_PIN, 0)
        if result < 0:
            raise RuntimeError(
                f"BCM-GPIO {GPIO_PIN} konnte nicht auf LOW gesetzt werden "
                f"({result})."
            )

        if prozent == 0:
            print("0 %: BCM-GPIO 17 bleibt auf LOW; keine Steuerimpulse.")
            return 0

        result = pi.wave_add_generic(erstelle_wellenform(prozent))
        if result < 0:
            raise RuntimeError(
                f"pigpio konnte die Impulse nicht zum Wellenformmuster "
                f"hinzufügen ({result})."
            )
        wave_id = pi.wave_create()
        if wave_id < 0:
            raise RuntimeError(
                f"pigpio konnte das Wellenformmuster nicht erstellen "
                f"(Fehlercode {wave_id})."
            )

        result = pi.wave_send_repeat(wave_id)
        if result < 0:
            raise RuntimeError(
                f"pigpio konnte das Wellenformmuster nicht starten "
                f"({result})."
            )
        print(
            f"{prozent} %: Wiederholendes Sekundenmuster an BCM-GPIO "
            f"{GPIO_PIN}; {prozent} von 100 Zeitfenstern ausgewählt, "
            f"jeweils {IMPULS_DAUER_US / 1000:g} ms."
        )
        print(
            "Achtung: Die tatsächliche Zahl geschalteter Halbwellen und die "
            "Leistung sind ohne Nulldurchgangsrückmeldung nicht exakt steuerbar."
        )
        print("Mit Strg+C beenden.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        pi.wave_tx_stop()
        pi.write(GPIO_PIN, 0)
        if wave_id is not None and wave_id >= 0:
            pi.wave_delete(wave_id)
        pi.stop()


if __name__ == "__main__":
    raise SystemExit(main())