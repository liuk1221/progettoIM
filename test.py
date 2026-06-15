import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


#Script per lanciare main.py su tutte le coppie Ollama definite nel .env.
#Legge sia le righe attive sia quelle commentate, cosi' il .env resta la matrice
#principale degli esperimenti e questo file si limita a eseguirla.


PROJECT_DIR = Path(__file__).resolve().parent
ENV_PATH = PROJECT_DIR / ".env"
CHAT_DIR = PROJECT_DIR / "chat" / "ScenarioMacerie_Rubble" #Si può cambiare per scenari diversi.


def parse_args():
    parser = argparse.ArgumentParser(
        description="Esegue tutte le combinazioni Ollama presenti nel .env."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Mostra le combinazioni rilevate senza eseguirle.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Salta le combinazioni che hanno gia' un file chat salvato.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Timeout in secondi per ogni esecuzione di main.py. Default: 900.",
    )
    return parser.parse_args()

def parse_ollama_combinations(env_path):
    #Raccoglie coppie explorer/medic anche se le righe sono commentate con "#".
    combinations = []
    seen = set()
    pending_explorer_model = None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        #Nel .env le combinazioni da testare sono commentate: tolgo solo il primo "#".
        if line.startswith("#"):
            line = line[1:].strip()

        if line.startswith("EXPLORER_OLLAMA_MODEL="):
            pending_explorer_model = line.split("=", 1)[1].strip()
            continue

        if line.startswith("MEDIC_OLLAMA_MODEL=") and pending_explorer_model:
            medic_model = line.split("=", 1)[1].strip()
            key = (pending_explorer_model, medic_model)
            if key not in seen:
                combinations.append(
                    {
                        "explorer_provider": "ollama",
                        "explorer_model": pending_explorer_model,
                        "medic_provider": "ollama",
                        "medic_model": medic_model,
                    }
                )
                seen.add(key)
            pending_explorer_model = None

    return combinations


def safe_filename_part(text):
    #Windows non accetta ":" e altri caratteri nei nomi file.
    safe = ""
    for char in str(text):
        if char.isalnum() or char in ("_", "-", "."):
            safe += char
        else:
            safe += "_"
    return safe or "unknown"


def chat_filename(combination):
    medic_provider = safe_filename_part(combination["medic_provider"])
    medic_model = safe_filename_part(combination["medic_model"])
    explorer_provider = safe_filename_part(combination["explorer_provider"])
    explorer_model = safe_filename_part(combination["explorer_model"])

    return (
        "chat_MED_"
        + medic_provider
        + "("
        + medic_model
        + ")_EXP_"
        + explorer_provider
        + "("
        + explorer_model
        + ").txt"
    )


def build_child_env(combination):
    #Le variabili impostate qui hanno precedenza sul .env caricato da main.py.
    child_env = os.environ.copy()
    child_env["EXPLORER_LLM"] = combination["explorer_provider"]
    child_env["MEDIC_LLM"] = combination["medic_provider"]
    child_env["EXPLORER_OLLAMA_MODEL"] = combination["explorer_model"]
    child_env["MEDIC_OLLAMA_MODEL"] = combination["medic_model"]
    child_env["RESCUE_FAST_UI"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    return child_env


def run_combination(combination, output_path, timeout):
    start_time = datetime.now()
    start_monotonic = time.monotonic()
    command = [sys.executable, "main.py"]

    header = [
        "Combinazione test",
        "=================",
        "Inizio: " + start_time.isoformat(timespec="seconds"),
        "Medico: "
        + combination["medic_provider"]
        + "/"
        + combination["medic_model"],
        "Esploratore: "
        + combination["explorer_provider"]
        + "/"
        + combination["explorer_model"],
        "Comando: " + " ".join(command),
        "",
    ]

    print("\n" + "\n".join(header), end="")
    transcript_parts = ["\n".join(header)]

    try:
        #Uso Popen invece di subprocess.run per mostrare la chat in tempo reale
        #e salvarla comunque nel file finale.
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_DIR),
            env=build_child_env(combination),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        timed_out = False
        while True:
            if time.monotonic() - start_monotonic > timeout:
                timed_out = True
                process.kill()
                break

            line = process.stdout.readline()
            if line:
                print(line, end="")
                transcript_parts.append(line)
                continue

            if process.poll() is not None:
                break

            time.sleep(0.1)

        remaining_output, _ = process.communicate()
        if remaining_output:
            print(remaining_output, end="")
            transcript_parts.append(remaining_output)

        if timed_out:
            raise subprocess.TimeoutExpired(command, timeout)

        exit_code = process.returncode
        end_time = datetime.now()

        footer = [
            "",
            "=================",
            "Fine: " + end_time.isoformat(timespec="seconds"),
            "Exit code: " + str(exit_code),
        ]
        footer_text = "\n".join(footer) + "\n"

        print(footer_text, end="")
        transcript_parts.append(footer_text)
        output_path.write_text("".join(transcript_parts), encoding="utf-8")
        return exit_code

    except subprocess.TimeoutExpired:
        end_time = datetime.now()
        timeout_text = (
            "\n\n=================\n"
            + "Fine: "
            + end_time.isoformat(timespec="seconds")
            + "\nTimeout dopo "
            + str(timeout)
            + " secondi\n"
        )

        print(timeout_text, end="")
        transcript_parts.append(timeout_text)
        output_path.write_text("".join(transcript_parts), encoding="utf-8")
        return None


def main():
    args = parse_args()

    if not ENV_PATH.exists():
        raise FileNotFoundError("File .env non trovato: " + str(ENV_PATH))

    combinations = parse_ollama_combinations(ENV_PATH)
    if not combinations:
        raise RuntimeError("Nessuna combinazione Ollama trovata nel .env.")

    if args.list:
        for index, combination in enumerate(combinations, start=1):
            print(
                str(index).zfill(2)
                + ". MED "
                + combination["medic_model"]
                + " / EXP "
                + combination["explorer_model"]
                + " -> "
                + chat_filename(combination)
            )
        return

    CHAT_DIR.mkdir(parents=True, exist_ok=True)

    print("Combinazioni trovate: " + str(len(combinations)))
    print("Cartella chat: " + str(CHAT_DIR))

    failures = []
    skipped = 0

    for index, combination in enumerate(combinations, start=1):
        output_path = CHAT_DIR / chat_filename(combination)
        label = (
            "MED "
            + combination["medic_model"]
            + " / EXP "
            + combination["explorer_model"]
        )

        if args.skip_existing and output_path.exists():
            skipped += 1
            print(str(index).zfill(2) + ". skip: " + label)
            continue

        print(str(index).zfill(2) + ". eseguo: " + label)
        exit_code = run_combination(combination, output_path, args.timeout)

        if exit_code == 0:
            print("    salvato: " + str(output_path))
        elif exit_code is None:
            failures.append((combination, "timeout"))
            print("    timeout, transcript salvato: " + str(output_path))
        else:
            failures.append((combination, "exit code " + str(exit_code)))
            print("    errore, transcript salvato: " + str(output_path))

    print("")
    print("Completato.")
    print("Saltate: " + str(skipped))
    print("Errori/timeout: " + str(len(failures)))

    if failures:
        print("Combinazioni con problemi:")
        for combination, reason in failures:
            print(
                "- MED "
                + combination["medic_model"]
                + " / EXP "
                + combination["explorer_model"]
                + ": "
                + reason
            )


if __name__ == "__main__":
    main()
