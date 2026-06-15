import subprocess
import sys
from pathlib import Path


class PlannerError(RuntimeError):
    pass


class PlannerResult:
    def __init__(self, source, actions, cost, raw_output=""):
        #source serve al display, actions al piano stampato, cost al confronto dei percorsi
        self.source = source
        self.actions = actions
        self.cost = cost
        self.raw_output = raw_output


def run_planner(
    domain_path,
    problem_path,
    project_dir,
):
    #Fast Downward e' sempre dentro la cartella del progetto.
    project_dir = Path(project_dir)
    domain_path = Path(domain_path)
    problem_path = Path(problem_path)
    fast_downward = project_dir / "fast-downward-24.06.1" / "fast-downward.py"
    if not fast_downward.exists():
        raise PlannerError("Fast Downward non trovato: " + str(fast_downward))

    return _run_fast_downward(fast_downward, domain_path, problem_path)


def _run_fast_downward(fast_downward,domain_path,problem_path):
    #Fast Downward scrive sas_plan nella sua directory: uso la cartella del problema generato
    plan_file = problem_path.parent / "sas_plan"

    #Rimuovo un piano precedente per evitare di leggere output vecchio dopo un errore.
    if plan_file.exists():
        plan_file.unlink()

    #Alias ottimale LM-cut, serve a cercare un piano a costo minimo
    #Questo è il comando con cui si lancia il run
    command = [
        sys.executable,
        str(fast_downward),
        "--alias",
        "seq-opt-lmcut",
        str(domain_path),
        str(problem_path),
    ]

    try:
        #capture_output permette di mostrare stdout/stderr in caso di errore
        completed = subprocess.run(
            command,
            cwd=str(problem_path.parent),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        raise PlannerError("Errore durante l'esecuzione di Fast Downward: " + str(exc))

    #Tengo l'output completo per diagnostica se sas_plan manca o non contiene azioni
    output = completed.stdout + "\n" + completed.stderr

    if not plan_file.exists():
        raise PlannerError(
            "Fast Downward non ha prodotto il file sas_plan.\n"
            + "Comando: "
            + " ".join(command)
            + "\nOutput:\n"
            + output
        )

    #Parsing minimale di sas_plan:
    # - righe con "(" sono azioni PDDL
    # - riga "; cost =" contiene il costo calcolato dal planner
    actions = []
    plan_cost = None
    for line in plan_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        clean = line.strip()

        if clean.startswith("("):
            actions.append(clean)
        elif clean.startswith("; cost ="):
            parts = clean.split()
            if len(parts) >= 4:
                try:
                    plan_cost = int(parts[3])
                except ValueError:
                    plan_cost = None

    if not actions:
        raise PlannerError(
            "Fast Downward ha prodotto sas_plan, ma non contiene azioni valide."
        )

    if plan_cost is None:
        plan_cost = len(actions)

    return PlannerResult("Fast Downward", actions, plan_cost, output)
