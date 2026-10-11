"""Liest die aktuelle Wirkleistung der Einspeisung aus einem KSEM per Modbus TCP."""

import argparse
import itertools
import socket
import struct
import sys
import time


REGISTER_EINSPEISELEISTUNG = 2
REGISTER_ANZAHL = 2
REGISTER_NETZLEISTUNG_START = 0
REGISTER_NETZLEISTUNG_ANZAHL = 4
EINHEIT_ID_STANDARD = 1
FAKTOR_WATT = 10
TRANSAKTIONS_IDS = itertools.count()


def exakt_lesen(verbindung: socket.socket, anzahl: int) -> bytes:
    daten = bytearray()
    while len(daten) < anzahl:
        teil = verbindung.recv(anzahl - len(daten))
        if not teil:
            raise ConnectionError("KSEM hat die Modbus-TCP-Verbindung geschlossen.")
        daten.extend(teil)
    return bytes(daten)


def lese_einspeiseleistung(
    adresse: str, port: int, einheit_id: int, timeout: float
) -> float:
    transaktions_id = next(TRANSAKTIONS_IDS) & 0xFFFF
    anfrage_pdu = struct.pack(
        ">BHH", 3, REGISTER_EINSPEISELEISTUNG, REGISTER_ANZAHL
    )
    kopf = struct.pack(">HHHB", transaktions_id, 0, len(anfrage_pdu) + 1, einheit_id)

    with socket.create_connection((adresse, port), timeout=timeout) as verbindung:
        verbindung.settimeout(timeout)
        verbindung.sendall(kopf + anfrage_pdu)

        antwort_kopf = exakt_lesen(verbindung, 7)
        antwort_id, protokoll_id, laenge, antwort_einheit = struct.unpack(
            ">HHHB", antwort_kopf
        )
        if antwort_id != transaktions_id:
            raise RuntimeError("Modbus-Antwort hat eine unerwartete Transaktions-ID.")
        if protokoll_id != 0:
            raise RuntimeError("KSEM-Antwort enthält eine ungültige Modbus-Protokoll-ID.")
        if antwort_einheit != einheit_id:
            raise RuntimeError("KSEM-Antwort hat eine unerwartete Modbus-Einheit-ID.")
        if laenge < 2:
            raise RuntimeError("KSEM-Antwort ist unvollständig.")

        pdu = exakt_lesen(verbindung, laenge - 1)

    funktion = pdu[0]
    if funktion == 0x83:
        if len(pdu) < 2:
            raise RuntimeError("KSEM hat einen ungültigen Modbus-Fehler gemeldet.")
        raise RuntimeError(f"KSEM meldet Modbus-Fehlercode {pdu[1]}.")
    if funktion != 3:
        raise RuntimeError(f"Unerwarteter Modbus-Funktionscode: {funktion}.")
    if len(pdu) != 6 or pdu[1] != 4:
        raise RuntimeError("KSEM hat nicht die erwarteten zwei Register geliefert.")

    rohwert = struct.unpack(">I", pdu[2:6])[0]
    return rohwert / FAKTOR_WATT


def lese_netzleistung(
    adresse: str, port: int, einheit_id: int, timeout: float
) -> float:
    """Liest Import und Einspeisung; liefert Einspeisung minus Netzbezug in W."""
    transaktions_id = next(TRANSAKTIONS_IDS) & 0xFFFF
    anfrage_pdu = struct.pack(
        ">BHH", 3, REGISTER_NETZLEISTUNG_START, REGISTER_NETZLEISTUNG_ANZAHL
    )
    kopf = struct.pack(">HHHB", transaktions_id, 0, len(anfrage_pdu) + 1, einheit_id)

    with socket.create_connection((adresse, port), timeout=timeout) as verbindung:
        verbindung.settimeout(timeout)
        verbindung.sendall(kopf + anfrage_pdu)

        antwort_kopf = exakt_lesen(verbindung, 7)
        antwort_id, protokoll_id, laenge, antwort_einheit = struct.unpack(
            ">HHHB", antwort_kopf
        )
        if antwort_id != transaktions_id:
            raise RuntimeError("Modbus-Antwort hat eine unerwartete Transaktions-ID.")
        if protokoll_id != 0:
            raise RuntimeError("KSEM-Antwort enthält eine ungültige Modbus-Protokoll-ID.")
        if antwort_einheit != einheit_id:
            raise RuntimeError("KSEM-Antwort hat eine unerwartete Modbus-Einheit-ID.")
        if laenge < 2:
            raise RuntimeError("KSEM-Antwort ist unvollständig.")

        pdu = exakt_lesen(verbindung, laenge - 1)

    funktion = pdu[0]
    if funktion == 0x83:
        if len(pdu) < 2:
            raise RuntimeError("KSEM hat einen ungültigen Modbus-Fehler gemeldet.")
        raise RuntimeError(f"KSEM meldet Modbus-Fehlercode {pdu[1]}.")
    if funktion != 3:
        raise RuntimeError(f"Unerwarteter Modbus-Funktionscode: {funktion}.")
    if len(pdu) != 10 or pdu[1] != 8:
        raise RuntimeError("KSEM hat nicht die erwarteten vier Register geliefert.")

    import_rohwert, export_rohwert = struct.unpack(">II", pdu[2:10])
    netzbezug_watt = import_rohwert / FAKTOR_WATT
    einspeisung_watt = export_rohwert / FAKTOR_WATT
    return einspeisung_watt - netzbezug_watt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zeigt laufend die Einspeiseleistung des KSEM an."
    )
    parser.add_argument("ip", help="IP-Adresse des KSEM")
    parser.add_argument("--port", type=int, default=502, help="Modbus-TCP-Port (Standard: 502)")
    parser.add_argument(
        "--einheit-id",
        type=int,
        default=EINHEIT_ID_STANDARD,
        help="Modbus-Einheit-ID (Standard: 1)",
    )
    parser.add_argument(
        "--intervall",
        type=float,
        default=1.0,
        help="Abfrageintervall in Sekunden (Standard: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Zeitlimit pro Modbus-Abfrage in Sekunden (Standard: 3)",
    )
    argumente = parser.parse_args()
    if not 1 <= argumente.port <= 65535:
        parser.error("--port muss zwischen 1 und 65535 liegen.")
    if not 0 <= argumente.einheit_id <= 255:
        parser.error("--einheit-id muss zwischen 0 und 255 liegen.")
    if argumente.intervall <= 0 or argumente.timeout <= 0:
        parser.error("--intervall und --timeout müssen größer als 0 sein.")

    print(f"Lese Einspeiseleistung vom KSEM {argumente.ip}; Abbruch mit Strg+C.")
    try:
        while True:
            try:
                watt = lese_einspeiseleistung(
                    argumente.ip,
                    argumente.port,
                    argumente.einheit_id,
                    argumente.timeout,
                )
                watt_text = f"{watt:.1f}".replace(".", ",")
                print(
                    f"\rAktuelle Einspeiseleistung: {watt_text} W   ",
                    end="",
                    flush=True,
                )
            except (OSError, RuntimeError) as fehler:
                print(f"\nFehler beim Lesen des KSEM: {fehler}", file=sys.stderr)
            time.sleep(argumente.intervall)
    except KeyboardInterrupt:
        print("\nBeendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
