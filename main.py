import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from src.llm_clients import create_llm_client
from src.planner import PlannerError, run_planner


#Classe paziente
class Patient:
    def __init__(self, patient_id, area_id, location, signs):
        self.patient_id = patient_id    #Id paziente
        self.area_id = area_id          #Id area in cui si trova il paziente
        self.location = location        #Location indicata come [riga,colonna]
        self.signs = signs              #Segni clinici osservati


#Classe area di intervento
class RescueArea:
    def __init__(self, area_id, location, observations, patient=None):
        self.area_id = area_id              #Id area sotto macerie
        self.location = location            #Location dell'area nella griglia [riga,colonna]
        self.observations = observations    #Osservazioni ambientali dell'area
        self.patient = patient              #Paziente presente nell'area


#Scenario generale
class Scenario:
    def __init__(self,name,rows,cols,medic_start,explorer_start,civilian_start,blocked_cells,rubble_gates,unsafe_cells,areas,triage_rules,):
        self.name = name                        #Nome dello scenario
        self.rows = rows                        #Righe griglia che rappresenta l'area crollata
        self.cols = cols                        #Colonne griglia
        self.medic_start = medic_start          #Punto inizio medico
        self.explorer_start = explorer_start    #Punto inizio esploratore (tipo cane soccorso)
        self.civilian_start = civilian_start    #Punto inizio soccorritore civile
        self.blocked_cells = blocked_cells      #Celle non attraversabili per crollo strutturale
        self.rubble_gates = rubble_gates        #Varchi ostruiti che soccorritore civile ed esploratore possono liberare
        self.unsafe_cells = unsafe_cells        #Celle da mettere in sicurezza prima del medico
        self.areas = areas                      #Aree dello scenario
        self.triage_rules = triage_rules        #Regole di triage usate dal fallback locale

    @property #Trasformo il metodo in un attributo (es. Scenario S. Posso usare s.patients)
    def patients(self): #Ritorno tutti i pazienti da tutte le aree.
        patients = []
        for area in self.areas:
            if area.patient is not None:
                patients.append(area.patient)
        return patients


#Classe per stampare info a terminale.
class Display:
    def __init__(self):
        self.fast_mode = os.getenv("RESCUE_FAST_UI") == "1"                     #Se imposto fast_ui ad 1 elimino la simulazione stile macchina da scrivere
        self.char_delay = float(os.getenv("RESCUE_CHAR_DELAY", "0.006"))        #Delay tra i caratteri.
        self.message_delay = float(os.getenv("RESCUE_MESSAGE_DELAY", "0.55"))   #Delay tra i messaggi.

    #Stampo il titolo passato raccolto da decoratori "="
    def section(self, title):
        print("\n" + "=" * 70)
        print(title)
        print("=" * 70)

    #Stampo un messaggio di sistema, metto un say con actor "sistema"
    def system(self, text):
        self.say("Sistema", text)

    #Stampo un messaggio detto da attore. Attore e' il mittente e text e' il messaggio.
    def say(self, actor, text):
        sys.stdout.write(f"\n[{actor}] ")
        sys.stdout.flush()
        self.line(text, end="") #Richiamo Line sotto per stampare il testo.
        sys.stdout.write("\n")
        sys.stdout.flush()

        if not self.fast_mode:
            time.sleep(self.message_delay)

    #Stampa il testo passato. Di def. vado a capo.
    def line(self, text="", end="\n"):
        #Se fast mode e' True scrivo direttamente il messaggio e faccio return
        if self.fast_mode:
            sys.stdout.write(text)
            sys.stdout.write(end)
            sys.stdout.flush()
            return

        #Stampo carattere per carattere con delay fisso RESCUE_CHAR_DELAY
        for char in text:
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(self.char_delay)

        #Alla fine vado a capo se end e' \n, come di default.
        sys.stdout.write(end)
        sys.stdout.flush()


#Classe report esplorazione
class ExplorationReport:
    def __init__(self, area_id, location, text, provider, model, used_fallback, error=None):
        self.area_id = area_id                  #Id area esplorata
        self.location = location                #Loc riga/colonna
        self.text = text                        #Descrizione text generata dall'esploratore
        self.provider = provider                #Servizio/provider LLM usato
        self.model = model                      #Nome del modello usato per generare il testo
        self.used_fallback = used_fallback      #True se e' stata usata la descrizione di fallback
        self.error = error                      #Eventuale errore avvenuto durante la generazione


#Classe richiesta chiarimenti
class ClarificationRequest:
    def __init__(self, questions, provider, model, used_fallback, error=None):
        self.questions = questions              #Domande formulate dal medico prima della scelta
        self.provider = provider                #Provider LLM usato per generare le domande
        self.model = model                      #Nome del modello usato
        self.used_fallback = used_fallback      #True se e' stato usato il fallback locale
        self.error = error                      #Eventuale errore avvenuto durante generazione/validazione


#Classe direzione prossima area
class AreaDirection:
    def __init__(self, area_id, reason, provider, model, used_fallback, error=None):
        self.area_id = area_id                  #Id area verso cui mandare l'esploratore
        self.reason = reason                    #Motivo della direzione data dal medico
        self.provider = provider                #Provider LLM usato per scegliere la prossima area
        self.model = model                      #Nome del modello usato
        self.used_fallback = used_fallback      #True se e' stata usata la fallback
        self.error = error                      #Eventuale errore avvenuto durante generazione/validazione


#Classe decisione dopo una stanza irraggiungibile
class ExplorationFlowDecision:
    def __init__(self, action, next_area_id, reason, provider, model, used_fallback, error=None):
        self.action = action                    #continue_exploration oppure final_decision
        self.next_area_id = next_area_id        #Prossima area se il medico decide di continuare
        self.reason = reason                    #Motivo operativo della scelta
        self.provider = provider                #Provider LLM usato
        self.model = model                      #Nome del modello usato
        self.used_fallback = used_fallback      #True se e' stata usata la fallback
        self.error = error                      #Eventuale errore avvenuto durante generazione/validazione


#Classe decisione triage
class TriageDecision:
    def __init__(self, selected_patient_id, priority_table, clarification_questions, explanation, provider, model, used_fallback, error=None):
        self.selected_patient_id = selected_patient_id          #Id paziente scelto come piu' urgente
        self.priority_table = priority_table                    #Lista ordinata delle priorita' dei pazienti
        self.clarification_questions = clarification_questions  #Domande usate prima della decisione finale
        self.explanation = explanation                          #Spiegazione della scelta fatta dal robot medico
        self.provider = provider                                #Provider LLM usato
        self.model = model                                      #Nome del modello usato per generare la decisione
        self.used_fallback = used_fallback                      #True se e' stata usata la fallback
        self.error = error                                      #Eventuale errore avvenuto durante generazione/validazione


class RescueRobot:
    #Classe generica che rappresenta un robot della simulazione.
    #Puo' comportarsi come esploratore o come medico, in base ai metodi usati.

    #Costruttore
    def __init__(self, llm, role_name):
        self.llm = llm                  #Riferimento a llm usato
        self.role_name = role_name      #Ruolo, esploratore o medico

    #Metodo parametrico generico per comporre un messaggio in linguaggio naturale.
    def compose_message(self, recipient, intent, context, fallback):
        #Costruzione prompt
        prompt = (
            "Sei il "
            + self.role_name #Ruolo del robot che parla
            + " in una simulazione di recupero sotto macerie. "
            "Scrivi in italiano un solo messaggio diretto al "
            + recipient #Specifica a chi e' diretto il messaggio.
            + ". "
            "Poche frasi, tono operativo, nessun markdown. " #Limitato per cercare di ridurre consumo di Token
            "Usa solo i dati nel contesto e non inventare dettagli.\n\n" #NON utilizzare informazioni inventate
            "Obiettivo del messaggio: "
            + intent #Scopo del messaggio a seconda del ruolo.
            + "\n\nContesto:\n"
            + context #Aggiunge il contesto disponibile da cui l'LLM deve ricavare il messaggio.
        )

        return self.llm.generate(prompt, fallback) #Tenta di generare con llm passato, altrimenti fallback.

    #Metodo usato dal robot esploratore per descrivere un'area.
    def describe_area(self, area):
        patient_text = "Nessun paziente visibile." #Testo di default se nell'area non e' presente alcun paziente.
        if area.patient is not None:
            patient_text = (
                "Paziente "
                + area.patient.patient_id #Inserisce l'id del paziente.
                + ": "
                + ", ".join(area.patient.signs) #Inserisce i segni osservati separati da virgole.
                + "."
            )

        #Serve se il modello non risponde o produce un errore.
        fallback = (
            "Nell'area "
            + area.area_id
            + ", posizione "
            + str(area.location)
            + ", noto "
            + ", ".join(area.observations) #Inserisce le osservazioni ambientali dell'area.
            + ". "
            + patient_text #Aggiunge le informazioni sul paziente, se presente.
        )

        #Costruisco il prompt esploratore
        prompt = (
            "Sei un robot esploratore in un edificio crollato. "
            "Descrivi in italiano solo quello che osservi nell'area. "
            "Non fare diagnosi e non scegliere il paziente piu' grave. "
            "Rendi la descrizione naturale, pero': niente cause, oggetti, "
            "persone, sintomi o pericoli inventati. Usa solo questi dati.\n\n"
            "Area: "
            + area.area_id
            + "\nPosizione: "
            + str(area.location)
            + "\nOsservazioni: "
            + str(area.observations)
            + "\nPaziente: "
            + patient_text
            + "\n"
        )
        result = self.llm.generate(prompt, fallback) #Genera descrizione area, altrimenti fallback.

        return ExplorationReport(
            area.area_id,
            area.location,
            result.text,
            result.provider,
            result.model,
            result.used_fallback,
            result.error,
        )

    #Metodo usato dal medico per preparare domande prima della scelta finale.
    def request_clarifications(self, reports, scenario):
        decision_scenario = scenario_from_reports(scenario, reports)
        local_request = make_local_clarification_request(decision_scenario) #Fallback locale con domande operative.
        report_text = reports_to_text(reports) #Riepilogo dei report esploratore.

        #Costruzione prompt: qui non si decide ancora il paziente.
        prompt = (
            "Sei un robot medico in una simulazione di recupero sotto macerie. "
            "Hai ricevuto i report dell'esploratore ma NON devi ancora scegliere "
            "il paziente prioritario. Formula domande di chiarimento che possano "
            "cambiare o confermare la decisione finale di triage. Chiedi dati "
            "clinici osservabili e rischi ambientali, senza chiedere percorsi.\n\n"
            "Scenario osservato:\n"
            + scenario_to_text(decision_scenario)
            + "\n\nReport ricevuti:\n"
            + report_text
            + "\nRispondi solo con questo JSON valido:\n"
            + "{\n"
            + '  "clarification_questions": ["..."]\n'
            + "}\n\n"
            + "Vincoli: massimo 5 domande, nessuna diagnosi inventata, nessuna "
            + "scelta del paziente prioritario in questa fase."
        )

        result = self.llm.generate_json(prompt, local_request) #LLM deve generare solo le domande.
        data = json.loads(result.text) #Converte la risposta JSON testuale in dizionario.
        data, validation_error = validate_clarification_request(data, local_request)

        used_fallback = result.used_fallback
        error = result.error
        if validation_error:
            data = local_request
            used_fallback = True
            error = validation_error

        return ClarificationRequest(
            data["clarification_questions"],
            result.provider,
            result.model,
            used_fallback,
            error,
        )

    #Metodo usato dal medico per scegliere la prossima area da esplorare.
    def choose_next_area(self, reports, unexplored_areas, current_location, scenario):
        local_direction = make_local_area_direction(
            scenario,
            unexplored_areas,
            current_location,
        )
        report_text = reports_to_text(reports)
        if not report_text.strip():
            report_text = "Nessun report ancora disponibile.\n"

        prompt = (
            "Sei un robot medico che guida un robot esploratore sotto macerie. "
            "Devi scegliere una sola prossima area da far raggiungere "
            "all'esploratore. Usa i report gia' ricevuti e la posizione attuale. "
            "Non scegliere il paziente prioritario e non pianificare tu il "
            "percorso: il percorso verra' calcolato da PDDL dopo questa scelta.\n\n"
            "Posizione attuale esploratore: "
            + str(current_location)
            + "\nAree non ancora esplorate:\n"
            + area_options_to_text(unexplored_areas)
            + "\nReport gia' ricevuti:\n"
            + report_text
            + "\nRispondi solo con questo JSON valido:\n"
            + "{\n"
            + '  "next_area_id": "uno degli id area non ancora esplorati",\n'
            + '  "reason": "motivo operativo breve"\n'
            + "}\n"
        )

        result = self.llm.generate_json(prompt, local_direction)
        data = json.loads(result.text)
        data, validation_error = validate_area_direction(
            data,
            unexplored_areas,
            local_direction,
        )

        used_fallback = result.used_fallback
        error = result.error
        if validation_error:
            data = local_direction
            used_fallback = True
            error = validation_error

        return AreaDirection(
            data["next_area_id"],
            str(data.get("reason", "")),
            result.provider,
            result.model,
            used_fallback,
            error,
        )

    #Metodo usato dal medico quando una stanza scelta non e' raggiungibile.
    def decide_after_unreachable_area(self, reports, unreachable_area, available_areas, current_location, scenario):
        local_decision = make_local_unreachable_decision(
            unreachable_area,
            available_areas,
            current_location,
        )
        report_text = reports_to_text(reports)
        if not report_text.strip():
            report_text = "Nessun report ancora disponibile.\n"

        available_text = area_options_to_text(available_areas)
        if not available_text.strip():
            available_text = "Nessuna location alternativa non esplorata.\n"

        prompt = (
            "Sei un robot medico che guida un robot esploratore sotto macerie. "
            "L'esploratore ti ha comunicato che una stanza richiesta non e' "
            "raggiungibile secondo il planner PDDL. Devi scegliere se "
            "terminare l'esplorazione e passare alla decisione finale oppure, "
            "solo se esiste una location alternativa non ancora esplorata, continuare "
            "verso una di quelle location. Non scegliere ancora il paziente "
            "prioritario in questa risposta.\n\n"
            "Posizione attuale esploratore: "
            + str(current_location)
            + "\nArea non raggiungibile:\n"
            + area_to_text(unreachable_area, scenario)
            + "\nLocation alternative non esplorate:\n"
            + available_text
            + "\nReport gia' ricevuti:\n"
            + report_text
            + "\nRispondi solo con questo JSON valido:\n"
            + "{\n"
            + '  "action": "continue_exploration oppure final_decision",\n'
            + '  "next_area_id": "id area alternativa oppure stringa vuota",\n'
            + '  "reason": "motivo operativo breve"\n'
            + "}\n\n"
            + "Vincoli: se non ci sono location alternative non esplorate, action "
            + "deve essere final_decision e next_area_id deve essere vuoto. Se "
            + "continui, next_area_id deve essere uno degli id alternativi."
        )

        result = self.llm.generate_json(prompt, local_decision)
        data = json.loads(result.text)
        data, validation_error = validate_unreachable_decision(
            data,
            available_areas,
            local_decision,
        )

        used_fallback = result.used_fallback
        error = result.error
        if validation_error:
            data = local_decision
            used_fallback = True
            error = validation_error

        return ExplorationFlowDecision(
            data["action"],
            data["next_area_id"],
            str(data.get("reason", "")),
            result.provider,
            result.model,
            used_fallback,
            error,
        )

    #Metodo usato dal medico per chiedere chiarimenti subito dopo un report.
    def request_area_clarifications(self, area, report, scenario):
        local_request = make_local_area_clarification_request(area, scenario)

        prompt = (
            "Sei un robot medico in una simulazione di recupero sotto macerie. "
            "Hai appena ricevuto il report di una singola area. Decidi se servono "
            "domande di chiarimento prima di mandare avanti l'esploratore. "
            "Se il report e' sufficiente, restituisci una lista vuota. Non "
            "scegliere ancora il paziente prioritario e non chiedere percorsi.\n\n"
            "Area appena esplorata:\n"
            + area_to_text(area, scenario)
            + "\nReport esploratore:\n"
            + report.text
            + "\nRispondi solo con questo JSON valido:\n"
            + "{\n"
            + '  "clarification_questions": ["..."]\n'
            + "}\n\n"
            + "Vincoli: massimo 3 domande, solo sulla situazione di questa area, "
            "nessuna diagnosi inventata."
        )

        result = self.llm.generate_json(prompt, local_request)
        data = json.loads(result.text)
        data, validation_error = validate_area_clarification_request(
            data,
            local_request,
        )

        used_fallback = result.used_fallback
        error = result.error
        if validation_error:
            data = local_request
            used_fallback = True
            error = validation_error

        return ClarificationRequest(
            data["clarification_questions"],
            result.provider,
            result.model,
            used_fallback,
            error,
        )

    #Metodo usato dal robot medico. Decide quale paziente deve essere soccorso per primo.
    def triage(self, reports, clarification_answers, scenario, asked_questions=None):
        decision_scenario = scenario_from_reports(scenario, reports)
        local_decision = make_local_triage(decision_scenario, clarification_answers) #Fallback locale che usa anche le risposte ai chiarimenti.
        report_text = reports_to_text(reports) #Stringa che contiene tutti i report.
        patient_text = patients_to_text(decision_scenario) #Riepilogo pazienti osservati e ambiente.
        if not patient_text.strip():
            patient_text = "Nessun paziente osservato nelle aree raggiunte.\n"
        clarification_text = clarifications_to_text(clarification_answers) #Domande e risposte gia' ottenute.

        #Costruzione del prompt per la decisione finale.
        prompt = (
            "Sei un robot medico in una simulazione di recupero sotto macerie. "
            "Ora devi decidere quale paziente raggiungere per primo. La decisione "
            "deve usare davvero sia i report iniziali sia le risposte alle domande "
            "di chiarimento. Le risposte dell'esploratore sono evidenze osservate "
            "e devono comparire nella motivazione se incidono sulla priorita'. "
            "Puoi includere nella decisione solo i pazienti presenti nelle aree "
            "raggiunte dall'esploratore e quindi descritti nei report. "
            "Non scegliere percorsi: il percorso e la stabilizzazione ambientale "
            "vengono decisi dal planner PDDL dopo la scelta del paziente.\n\n"
            "Dati pazienti osservati nelle aree raggiunte:\n"
            + patient_text
            + "\nReport ricevuti:\n"
            + report_text
            + "\nChiarimenti ottenuti prima della decisione:\n"
            + clarification_text
            + "\nStabilisci tu le regole di priorita' in base a cosa pensi sia "
            "giusto interpretando il ruolo di un medico.\n"
            + "\nRispondi solo con questo JSON valido:\n"
            + "{\n"
            + '  "selected_patient_id": "uno degli id paziente disponibili",\n'
            + '  "priority_table": [{"patient_id": "id_paziente", "priority": 1, "reason": "..."}],\n'
            + '  "clarification_questions": ["domande gia usate nella decisione"],\n'
            + '  "explanation": "..."\n'
            + "}\n\n"
            + "Vincoli: usa solo gli id paziente osservati nelle aree raggiunte, "
            + "includi tutti e soli questi pazienti una sola volta nella "
            "priority_table e assegna priority 1 al paziente piu' urgente. Il "
            "campo selected_patient_id deve essere identico al patient_id della "
            "riga con priority 1. La priority_table deve avere "
            + "esattamente "
            + str(len(decision_scenario.patients))
            + " righe."
        )

        result = self.llm.generate_json(prompt, local_decision) #LLM deve generare la decisione finale in JSON.
        data = json.loads(result.text) #Converte la risposta JSON testuale in un dizionario Python.

        #A questo punto la risposta viene validata.
        # - JSON con campi corretti
        # - pazienti esistenti
        # - tutti i pazienti presenti
        # - numero corretto di righe
        # - priorita' corrette
        data, validation_error = validate_triage_decision(
            data,
            decision_scenario,
            local_decision,
        )

        used_fallback = result.used_fallback
        error = result.error

        #Se non ci sono errori di validazione, controlla la coerenza logica.
        if not validation_error:
            consistency_error = find_triage_consistency_error(data, decision_scenario) #Cerca eventuali incoerenze interne.
            #Se le trova allora rilancia un altro prompt per correggere gli errori.
            #Se non trova inconsistenze è None
            if consistency_error:
                repaired = self.repair_triage_decision(data, reports, clarification_answers, decision_scenario, local_decision, consistency_error)
                repair_data = json.loads(repaired.text) #Conversione
                #Validazione numero 2
                repair_data, validation_error = validate_triage_decision( repair_data,decision_scenario,local_decision)
                #Se la decisione riparata e' valida, controlla di nuovo la coerenza.
                if not validation_error:
                    repair_consistency_error = find_triage_consistency_error(repair_data,decision_scenario)
                    #Se durante la riparazione e' stato usato il fallback,
                    if repaired.used_fallback:
                        data["explanation"] = build_consistent_triage_explanation(data)
                        #Ricostruisce una spiegazione coerente con la decisione attuale.
                    elif repair_consistency_error:
                        #Se la riparazione e' ancora incoerente, non usa repair_data come nuova decisione.
                        data["explanation"] = build_consistent_triage_explanation(data)
                        #Ricostruisce solo la spiegazione della decisione originale.
                    else:
                        #Se la riparazione e' valida e coerente,
                        #sostituisce la decisione originale con quella riparata.
                        data = repair_data
                        used_fallback = repaired.used_fallback
                        error = repaired.error
                else:
                    #Se la decisione riparata non e' valida, mantiene la decisione precedente.
                    data["explanation"] = build_consistent_triage_explanation(data)
                    validation_error = None #Annulla l'errore di validazione della riparazione.

        if validation_error:
            #Se alla fine esiste ancora un errore di validazione, utilizzo la locale senza llm
            data = local_decision
            used_fallback = True
            error = validation_error

        if asked_questions and not data.get("clarification_questions"):
            data["clarification_questions"] = list(asked_questions)

        #Classe risposta
        return TriageDecision(
            data["selected_patient_id"],
            data["priority_table"],
            data.get("clarification_questions", []),
            str(data.get("explanation", "")),
            result.provider,
            result.model,
            used_fallback,
            error,
        )

    #Metodo che prova a correggere una decisione di triage incoerente. La verifica è locale.
    def repair_triage_decision(self, decision, reports, clarification_answers, scenario, local_decision, issue):
        prompt = (
            "Sei un robot medico. Hai prodotto una decisione di triage in JSON, "
            "ma contiene questa incoerenza: "
            + issue #Specifica qual e' il problema trovato nella decisione precedente.
            + "\n\nCorreggi la decisione usando solo i dati osservati. "
            "Non inventare segni o osservazioni. La spiegazione deve essere "
            "coerente con selected_patient_id e con la riga priority 1. "
            "Mantieni il piu' possibile la scelta e l'ordine gia' dati, ma "
            "correggi qualunque contraddizione.\n\n"
            "Scenario osservato:\n"
            + scenario_to_text(scenario)
            + "\n\nReport ricevuti:\n"
            + reports_to_text(reports)
            + "\nChiarimenti ottenuti prima della decisione:\n"
            + clarifications_to_text(clarification_answers)
            + "\n\nDecisione da correggere:\n"
            + json.dumps(decision, ensure_ascii=False, indent=2)
            + "\n\nRispondi solo con questo JSON valido:\n"
            + "{\n"
            + '  "selected_patient_id": "uno degli id paziente disponibili",\n'
            + '  "priority_table": [{"patient_id": "id_paziente", "priority": 1, "reason": "..."}],\n'
            + '  "clarification_questions": ["domande gia usate nella decisione"],\n'
            + '  "explanation": "..."\n'
            + "}\n"
        )

        return self.llm.generate_json(prompt, local_decision)#Rigenero JSON da llm.

    #Metodo usato dall'esploratore per rispondere a una domanda del medico.
    def answer_clarification(self, question, scenario):
        fallback = make_local_clarification_answer(question, scenario) #Creo risposta locale senza llm per fallback.

        prompt = (
            "Sei un robot esploratore in un edificio crollato. Rispondi in italiano "
            "alla domanda del robot medico usando solo i dati osservati nello "
            "scenario. Non inventare nuovi sintomi, diagnosi o dettagli ambientali. "
            "Se la domanda non e' verificabile dai dati, dillo chiaramente.\n\n"
            "Domanda del medico: "
            + question
            + "\n\nScenario osservato:\n"
            + scenario_to_text(scenario)
        )

        return self.llm.generate(prompt, fallback) #Risposta con llm, altrimenti fallback


#Funzione di caricamento dello scenario, apre il json che definisce il mondo dato il path.
def load_scenario(path):
    #Passo il path dello scenario e lo apro con json.load
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)

    #Inizializzo la lista vuota, diventera' una lista di RescueArea
    #Area è vuota inizialmente e poi viene popolata
    areas = []
    area_items = data["areas"]
    for area_data in area_items:
        #Carico la location come tupla
        location = tuple(area_data["location"]) #Non modificabile, migliore per coordinate come MultiRobotGrid
        patient = None #Di def. None

        #Se il patient c'è nei data di quella area allora lo salvo in patient.
        if "patient" in area_data:
            patient_data = area_data["patient"]
            patient = Patient(
                patient_data["id"],
                area_data["id"],
                location,
                list(patient_data["signs"]),
            )

        #Salvo in RescueArea l'area attuale con tutte le info
        area = RescueArea(
            area_data["id"],
            location,
            list(area_data["observations"]),
            patient, #Di tipo Patient
        )
        #Aggiungo area a areas
        areas.append(area)

    #Imposto le celle bloccate e non attraversabili.
    blocked_cells = set()
    for cell in data.get("blocked_cells", []):
        blocked_cells.add(tuple(cell))

    #Imposto i varchi con macerie che soccorritore civile ed esploratore possono liberare.
    rubble_gates = set() #Rubble gates è fatto da elementi di questo tipo [[prima cella],[seconda cella]], ...
    for gate in data.get("rubble_gates", []):
        rubble_gates.add(normalize_rubble(tuple(gate[0]), tuple(gate[1]))) 
        #Normalizzo ordinando le due tuple che rappresentano le coordinate delle due celle

    #Imposto le celle da mettere in sicurezza prima dell'ingresso del medico.
    unsafe_cells = set()
    for cell in data.get("unsafe_cells", []):
        unsafe_cells.add(tuple(cell))

    #Salvo le regole di triage, utilizzate per il calcolo locale senza llm
    triage_rules = data.get("triage_rules", {})

    #Ritorno lo scenario.
    return Scenario(
        data["name"],
        int(data["rows"]),
        int(data["cols"]),
        tuple(data["medic_start"]),
        tuple(data["explorer_start"]),
        tuple(data.get("civilian_start", data["explorer_start"])),
        blocked_cells,
        rubble_gates,
        unsafe_cells,
        areas,
        triage_rules,
    )

#Passo due celle e creo una chiave unica per il varco.
def normalize_rubble(first, second):
    if first <= second:
        return (first, second)
    return (second, first)


#FIND BY ID
#Passo uno scenario e un area_id
def find_area_by_id(scenario, area_id):
    for area in scenario.areas:
        if area.area_id == area_id:
            return area

    #Se non ho fatto return, errore
    raise ValueError("Area non trovata: " + area_id)
#Passo uno scenario e un patient_id
def find_patient_by_id(scenario, patient_id):
    for patient in scenario.patients:
        if patient.patient_id == patient_id:
            return patient

    #Se non ho fatto return, errore
    raise ValueError("Paziente non trovato: " + patient_id)


#Imposto la label per il display del risultato
def provider_label(result):
    #Se l'indicatore di uso fallback e' True, allora concateno provider + fallback per sapere che non e' stato usato llm
    if result.used_fallback:
        return result.provider + " fallback"
    #Ritorno il nome provider
    return result.provider

#Passo il display (il terminale) e il risultato
def show_llm_diagnostic(display, result):
    #Se ho usato fallback e ho un errore, allora mostro l'errore che ha causato fallback ritornato dal modello llm
    if result.used_fallback and result.error:
        display.system(
            "Fallback "
            + result.provider
            + " ("
            + result.model
            + "): "
            + result.error
        )
#Passo display, actor (medico o esploratore) e risultato
def say_llm_result(display, actor, result):
    display.say(actor + " (" + provider_label(result) + ")", result.text)
    show_llm_diagnostic(display, result) #Lo metto sempre ma poi mostra solo se effettivamente ho avuto errori

#METODI TO TEXT
#Converte oggetto scenario in una stringa
def scenario_to_text(scenario):
    #Utile per llm o per display
    lines = ["Scenario: " + scenario.name]
    lines.append("Medico start=" + str(scenario.medic_start))
    lines.append("Soccorritore civile start=" + str(scenario.civilian_start))

    #Scorro tutte le aree dello scenario
    for area in scenario.areas:
        line = (
            "- area="
            + area.area_id
            + " posizione="
            + str(area.location)
            + "; osservazioni="
            + str(area.observations)
        )
        #Controlla se nell'area e' presente un paziente
        if area.patient is not None:
            line += (
                "; paziente="
                + area.patient.patient_id
                + "; segni="
                + str(area.patient.signs)
            )
        if area.location in scenario.unsafe_cells:
            line += "; stato=area da mettere in sicurezza"
        #Aggiungo alla lista di stringhe
        lines.append(line)

    return "\n".join(lines) #Con join unisco tutto in un unica stringa, invece che in una lista di stringhe.
#Converte i report in testo compatto
def reports_to_text(reports):
    text = ""
    for report in reports:
        text += "- " + report.area_id + ": " + report.text + "\n"
    return text
#Converte i pazienti in testo compatto
def patients_to_text(scenario):
    patient_text = ""
    for patient in scenario.patients: #Scorre tutti i pazienti presenti nello scenario.
        area = find_area_by_id(scenario, patient.area_id) #Cerco l'area in cui si trova il paziente.
        patient_text += (
            "- "
            + patient.patient_id
            + " in "
            + patient.area_id
            + ": segni="
            + str(patient.signs)
            + "; ambiente="
            + str(area.observations)
        )
        if area.location in scenario.unsafe_cells:
            patient_text += "; area non ancora messa in sicurezza"
        patient_text += "\n"
    return patient_text
#Converte domande e risposte di chiarimento in testo compatto
def clarifications_to_text(clarification_answers):
    if not clarification_answers:
        return "Nessun chiarimento disponibile.\n"

    text = ""
    for exchange in clarification_answers:
        text += "- Domanda: " + exchange["question"] + "\n"
        text += "  Risposta: " + exchange["answer"] + "\n"
    return text
#Formato singola area per prompt e chiarimenti
def area_to_text(area, scenario):
    text = (
        "- area="
        + area.area_id
        + " posizione="
        + str(area.location)
        + "; osservazioni="
        + str(area.observations)
    )
    if area.patient is not None:
        text += (
            "; paziente="
            + area.patient.patient_id
            + "; segni="
            + str(area.patient.signs)
        )
    if area.location in scenario.unsafe_cells:
        text += "; stato=area da mettere in sicurezza"
    return text
#Elenco sintetico delle zone candidate per la prossima esplorazione.
def area_options_to_text(areas):
    lines = []
    for area in areas:
        lines.append(
            "- "
            + area.area_id
            + " posizione="
            + str(area.location)
        )

    return "\n".join(lines) + "\n"

#Vista dello scenario che contiene solo le aree realmente raggiunte.
#Serve quando il triage deve ignorare aree senza report.
def scenario_from_reports(scenario, reports):
    observed_area_ids = set()
    for report in reports:
        observed_area_ids.add(report.area_id)

    observed_areas = []
    for area in scenario.areas:
        if area.area_id in observed_area_ids:
            observed_areas.append(area)

    return Scenario(
        scenario.name + " - aree osservate",
        scenario.rows,
        scenario.cols,
        scenario.medic_start,
        scenario.explorer_start,
        scenario.civilian_start,
        scenario.blocked_cells,
        scenario.rubble_gates,
        scenario.unsafe_cells,
        observed_areas, #Qui andrebe areas, e invece passo solo quelle osservate davvero
        scenario.triage_rules,
    )

#Serve per calcolare la distanza manhattan per la fallback
def manhattan_distance(first, second):
    #Distanza su griglia ortogonale: non considera diagonali.
    return abs(first[0] - second[0]) + abs(first[1] - second[1])
#Fallback: se il medico LLM non risponde.
#esploro la zona piu' vicina in termini di distanza manhattan nella griglia.
def make_local_area_direction(scenario, unexplored_areas, current_location):
    nearest_area = None
    nearest_distance = None

    for area in unexplored_areas:
        distance = manhattan_distance(current_location, area.location)
        if nearest_distance is None or distance < nearest_distance:
            nearest_area = area
            nearest_distance = distance

    if nearest_area is None:
        return {
            "next_area_id": "",
            "reason": "Nessuna area rimasta da esplorare.",
        }

    return {
        "next_area_id": nearest_area.area_id,
        "reason": (
            "Fallback locale: scelgo l'area non esplorata piu' vicina alla "
            "posizione attuale dell'esploratore."
        ),
    }

#Controlla che il medico abbia scelto una zona ancora da esplorare.
def validate_area_direction(data, unexplored_areas, local_direction):
    if not isinstance(data, dict):
        return local_direction, "Direzione LLM non valida: JSON non oggetto."

    valid_area_ids = []
    for area in unexplored_areas:
        valid_area_ids.append(area.area_id)

    area_id = data.get("next_area_id")
    if area_id not in valid_area_ids:
        return local_direction, "Direzione LLM non valida: area assente o gia' esplorata."

    reason = str(data.get("reason", "")).strip()
    if not reason:
        reason = "Prossima area scelta dal medico in base ai report disponibili."

    return {"next_area_id": area_id, "reason": reason}, None

#Fallback: continua solo se esiste almeno una location alternativa.
def make_local_unreachable_decision(unreachable_area, available_areas, current_location):
    nearest_area = None
    nearest_distance = None

    #Scorre tutte le aree ancora disponibili e calcola la distanza tra la posizione attuale e ogni area
    for area in available_areas:
        distance = manhattan_distance(current_location, area.location)
        if nearest_distance is None or distance < nearest_distance:
            nearest_area = area
            nearest_distance = distance

    #Non ho alternative. Faccio il triage finale con i dati che ho
    if nearest_area is None:
        return {
            "action": "final_decision",
            "next_area_id": "",
            "reason": (
                "La stanza "
                + unreachable_area.area_id
                + " non e' raggiungibile e non risultano altre location "
                "non esplorate. Passo alla decisione finale con i dati disponibili."
            ),
        }

    #Continuo l'esplorazione con la prossima più vicina
    return {
        "action": "continue_exploration",
        "next_area_id": nearest_area.area_id,
        "reason": (
            "La stanza "
            + unreachable_area.area_id
            + " non e' raggiungibile. Continuo verso la location alternativa "
            "non esplorata piu' vicina."
        ),
    }

#Normalizza la scelta del medico dopo un blocco del percorso esplorativo.
def validate_unreachable_decision(data, available_areas, local_decision):
    #Se data non è un dizionario, la funzione non può leggerla bene
    if not isinstance(data, dict):
        return local_decision, "Decisione post-blocco non valida: JSON non oggetto."

    #Qui crea una lista con gli ID delle aree alternative ancora non esplorate
    valid_area_ids = []
    for area in available_areas:
        valid_area_ids.append(area.area_id)

    action = normalize_text(str(data.get("action", ""))).replace(" ", "_")
    continue_aliases = [
        "continue_exploration",
        "continue",
        "continua",
        "continuare",
        "prosegui",
        "proseguire",
    ]
    final_aliases = [
        "final_decision",
        "terminate_exploration",
        "termina",
        "terminare",
        "stop",
        "decisione_finale",
        "triage_finale",
    ]

    #prende qualunque alias valido e lo converte in una delle due forme ufficiali
    if action in continue_aliases:
        action = "continue_exploration"
    elif action in final_aliases:
        action = "final_decision"
    else:
        return local_decision, "Decisione post-blocco non valida: action sconosciuta."

    #Se LLM ha dato una spiegazione con "reason", viene usata quella
    reason = str(data.get("reason", "")).strip()
    if not reason:
        reason = str(local_decision.get("reason", "")).strip()

    #e non ci sono aree alternative, la funzione forza la decisione finale
    #anche se llm dice di continuare, non posso
    if not valid_area_ids:
        reason = str(local_decision.get("reason", "")).strip()
        return {
                "action": "final_decision",
                "next_area_id": "",
                "reason": reason
                or "Non ci sono location alternative non esplorate.",
            }, None

    #Se la decisione è terminare l’esplorazione, la funzione la accetta
    if action == "final_decision":
        return {
            "action": "final_decision",
            "next_area_id": "",
            "reason": reason or "Termino l'esplorazione e passo al triage finale.",
        }, None
    #Se voglio continuare ci deve essere anche una prossima zona valida
    next_area_id = str(data.get("next_area_id", "")).strip()
    if next_area_id not in valid_area_ids:
        return local_decision, "Decisione post-blocco non valida: area alternativa assente."

    return {
        "action": "continue_exploration",
        "next_area_id": next_area_id,
        "reason": reason or "Continuo verso una location alternativa raggiungibile.",
    }, None

#Domande minime che il medico puo' fare subito dopo il report di una zona.
#Si utilizza solo ed unicamente come FALLBACK.
def make_local_area_clarification_request(area, scenario):
    questions = []
    if area.patient is not None:
        signs = area.patient.signs[:2]
        if signs:
            questions.append(
                "Puoi confermare in "
                + area.area_id
                + " per "
                + area.patient.patient_id
                + " i segni osservati: "
                + ", ".join(signs)
                + "?"
            )
    if area.location in scenario.unsafe_cells or area_has_environmental_risk(area):
        questions.append(
            "Nell'area "
            + area.area_id
            + " ci sono ancora pericoli ambientali immediati osservabili?"
        )
    return {"clarification_questions": questions[:3]}

#Ricerca parole chiave ambientali che meritano chiarimento al medico.
#Viene chiamata solo dalla FALLBACK sopra
def area_has_environmental_risk(area):
    risk_markers = [
        "macerie",
        "ostruito",
        "instabili",
        "lesionata",
        "polvere",
        "cedimento",
    ]
    observations = normalize_text(" ".join(area.observations))
    for marker in risk_markers:
        if marker in observations:
            return True

    return False


def validate_area_clarification_request(data, local_request):
    #Normalizza il JSON del medico e limita il numero di domande per area.
    if not isinstance(data, dict):
        return local_request, "Chiarimenti area non validi: JSON non oggetto."

    questions = data.get("clarification_questions")
    if not isinstance(questions, list):
        return local_request, "Chiarimenti area non validi: lista mancante."

    normalized_questions = []
    for question in questions:
        text = str(question).strip()
        if text:
            normalized_questions.append(text)

    return {"clarification_questions": normalized_questions[:3]}, None

#Legge il punteggio locale associato a un segno o a una osservazione.
def get_rule_score(scenario, group, text, default_score):
    group_rules = scenario.triage_rules.get(group, {})
    return int(group_rules.get(str(text).lower(), default_score))
#Prepara fallback locale per le domande del medico.
def make_local_clarification_request(scenario):
    return {
        "clarification_questions": make_local_clarification_questions(scenario)
    }
#Crea domande locali senza decidere prima la priorita'.
def make_local_clarification_questions(scenario):
    questions = []

    #Chiede conferma dei segni principali per ogni paziente.
    for patient in scenario.patients:
        signs = patient.signs[:2]
        if signs:
            questions.append(
                "Puoi confermare per "
                + patient.patient_id
                + " i segni osservati: "
                + ", ".join(signs)
                + "?"
            )

    #Chiede conferma delle aree instabili perche' possono incidere sulla gestione.
    for area in scenario.areas:
        if area.location in scenario.unsafe_cells and area.patient is not None:
            questions.append(
                "L'area "
                + area.area_id
                + " del paziente "
                + area.patient.patient_id
                + " presenta ancora macerie instabili o pericoli immediati?"
            )

    return questions[:5]
#Calcola un bonus se le risposte confermano segni o rischi gia' osservati.
def score_clarification_evidence(scenario, patient, area, clarification_answers):
    bonus = 0
    patient_marker = patient.patient_id.lower()
    area_marker = area.area_id.lower()

    for exchange in clarification_answers:
        combined = normalize_text(exchange["question"] + " " + exchange["answer"])
        if patient_marker not in combined and area_marker not in combined:
            continue

        for sign in patient.signs:
            if contains_full_fact(combined, normalize_text(sign)):
                rule_score = get_rule_score(scenario, "signs", sign, 5)
                if rule_score > 0:
                    bonus += max(1, int(rule_score / 10))

        for observation in area.observations:
            if contains_full_fact(combined, normalize_text(observation)):
                rule_score = get_rule_score(scenario, "observations", observation, 0)
                if rule_score > 0:
                    bonus += max(1, int(rule_score / 10))

    return bonus
#Metodo per prendere una decisione locale senza llm, serve anche questa da FALLBACK
def make_local_triage(scenario, clarification_answers=None):
    clarification_answers = clarification_answers or []
    scored_patients = []

    #Per ogni paziente dello scenario
    for patient in scenario.patients:
        #Prendi l'area
        area = find_area_by_id(scenario, patient.area_id)
        #Score base a 0
        score = 0

        #Per ogni segno, con la tabella delle regole triage, assegna il punteggio
        for sign in patient.signs:
            score += get_rule_score(scenario, "signs", sign, 5)

        #Anche l'ambiente incrementa o decrementa lo score.
        for observation in area.observations:
            score += get_rule_score(scenario, "observations", observation, 0)

        #Le risposte ai chiarimenti aumentano il peso dei dati confermati.
        clarification_bonus = score_clarification_evidence(
            scenario,
            patient,
            area,
            clarification_answers,
        )
        score += clarification_bonus

        scored_patients.append((score, clarification_bonus, patient))

    #Ordino i pazienti in base allo score
    scored_patients.sort(key=lambda item: item[0], reverse=True)

    if not scored_patients:
        return {
            "selected_patient_id": "",
            "priority_table": [],
            "clarification_questions": [],
            "explanation": (
                "Nessun paziente e' stato osservato nelle aree raggiunte: "
                "non posso assegnare una priorita' medica."
            ),
        }

    #Faccio una tabella di priorita'. Inizializzo la lista vuota
    priority_table = []
    for index, item in enumerate(scored_patients, start=1):
        score = item[0]
        clarification_bonus = item[1]
        patient = item[2]
        reason = (
            "Score "
            + str(score)
            + " calcolato da segni, ambiente e chiarimenti ricevuti."
        )
        if clarification_bonus > 0:
            reason += " I chiarimenti aggiungono +" + str(clarification_bonus) + "."

        priority_table.append(
            {
                "patient_id": patient.patient_id,
                "priority": index,
                "reason": reason,
            }
        )

    selected_patient = scored_patients[0][2]

    return {
        "selected_patient_id": selected_patient.patient_id,
        "priority_table": priority_table,
        "clarification_questions": make_local_clarification_questions(scenario),
        "explanation": (
            "Fallback locale: sceglie il paziente con lo score piu' alto dopo "
            "aver integrato le risposte di chiarimento disponibili."
        ),
    }

#Valida il JSON delle domande di chiarimento.
#Local request è la fallback in caso il modello non lavora bene.
def validate_clarification_request(data, local_request):
    if not isinstance(data, dict):
        return local_request, "Domande LLM non valide: JSON non oggetto."

    questions = data.get("clarification_questions")
    if not isinstance(questions, list):
        return local_request, "Domande LLM non valide: clarification_questions mancante."

    normalized_questions = []
    for question in questions:
        text = str(question).strip()
        if text:
            normalized_questions.append(text)

    if not normalized_questions:
        return local_request, "Domande LLM non valide: lista vuota."

    return {"clarification_questions": normalized_questions[:5]}, None

#Validazione triage: il modello deve restituire tutti i pazienti una sola volta
#Local decision è la fallback in caso qualcosa dai dati llm non va bene.
def validate_triage_decision(data, scenario, local_decision):
    patient_ids = []
    for patient in scenario.patients:
        patient_ids.append(patient.patient_id)

    if not isinstance(data, dict):
        return local_decision, "Decisione LLM non valida: JSON non oggetto."

    #Accetta anche nomi campo leggermente diversi generati dai modelli locali.
    data = normalize_triage_keys(data)
    selected_patient_id = normalize_patient_id(data.get("selected_patient_id"), patient_ids)

    table = data.get("priority_table")
    if not isinstance(table, list):
        return local_decision, "Decisione LLM non valida: priority_table mancante."

    #Ricostruisce una tabella pulita: id paziente, priorita' numerica e motivo.
    normalized_table = []
    seen_patients = set()
    for item in table:
        if not isinstance(item, dict):
            return local_decision, "Decisione LLM non valida: riga priorita' non oggetto."

        item = normalize_triage_row_keys(item)
        patient_id = normalize_patient_id(item.get("patient_id"), patient_ids)
        if patient_id not in patient_ids or patient_id in seen_patients:
            return local_decision, (
                "Decisione LLM non valida: paziente duplicato o sconosciuto."
            )

        try:
            priority = int(item.get("priority"))
        except Exception:
            priority = len(normalized_table) + 1

        reason = str(item.get("reason", "")).strip()
        if not reason:
            reason = "Motivazione non strutturata dal modello; riga mantenuta dalla tabella LLM."

        seen_patients.add(patient_id)
        normalized_table.append(
            {
                "patient_id": patient_id,
                "priority": priority,
                "reason": reason,
            }
        )

    #Riordino in base alla priorita' dichiarata dal modello.
    normalized_table.sort(key=lambda item: item["priority"])
    missing_patient_ids = []
    for patient_id in patient_ids:
        if patient_id not in seen_patients:
            missing_patient_ids.append(patient_id)

    #Se il modello dimentica un paziente, completo la tabella con il fallback locale.
    for local_item in local_decision["priority_table"]:
        if local_item["patient_id"] in missing_patient_ids:
            normalized_table.append(
                {
                    "patient_id": local_item["patient_id"],
                    "priority": len(normalized_table) + 1,
                    "reason": (
                        "Completamento locale: il modello non ha incluso questo "
                        "paziente nella tabella."
                    ),
                }
            )

    if not normalized_table:
        return local_decision, "Decisione LLM non valida: priority_table vuota."

    if selected_patient_id not in patient_ids:
        selected_patient_id = normalized_table[0]["patient_id"]

    #Il paziente selezionato deve coincidere con la riga di priorita' 1.
    if normalized_table[0]["patient_id"] != selected_patient_id:
        selected_patient_id = normalized_table[0]["patient_id"]

    for index, item in enumerate(normalized_table, start=1):
        item["priority"] = index

    questions = data.get("clarification_questions", [])
    if not isinstance(questions, list):
        questions = []

    normalized_questions = []
    for question in questions:
        text = str(question).strip()
        if text:
            normalized_questions.append(text)

    explanation = str(data.get("explanation", "")).strip()
    if not explanation:
        explanation = build_consistent_triage_explanation(
            {
                "selected_patient_id": selected_patient_id,
                "priority_table": normalized_table,
            }
        )

    return (
        {
            "selected_patient_id": selected_patient_id,
            "priority_table": normalized_table,
            "clarification_questions": normalized_questions,
            "explanation": explanation,
        },
        None,
    )

#Mappa alias italiani/inglesi sui nomi del programma
#Aggiunta in debugging per correggere alcuni comportamenti non corretti da parte llm.
#Può essere tolta in caso
def normalize_triage_keys(data):
    normalized = dict(data)
    key_aliases = {
        "selected_patient_id": [
            "selected_patient_id",
            "selected_patient",
            "patient_id",
            "paziente_selezionato",
            "paziente_prioritario",
            "id_paziente_selezionato",
        ],
        "priority_table": [
            "priority_table",
            "priorities",
            "priority",
            "triage",
            "tabella_priorita",
            "tabella_priorità",
            "classifica",
        ],
        "clarification_questions": [
            "clarification_questions",
            "questions",
            "domande_chiarimento",
            "domande",
        ],
        "explanation": [
            "explanation",
            "reason",
            "motivation",
            "motivazione",
            "spiegazione",
        ],
    }

    for canonical_key, aliases in key_aliases.items():
        if canonical_key in normalized:
            continue
        for alias in aliases:
            if alias in normalized:
                normalized[canonical_key] = normalized[alias]
                break

    return normalized

#Stessa normalizzazione sopra, ma applicata alle singole righe della tabella
def normalize_triage_row_keys(row):
    normalized = dict(row)
    key_aliases = {
        "patient_id": [
            "patient_id",
            "selected_patient_id",
            "patient",
            "paziente",
            "id_paziente",
        ],
        "priority": [
            "priority",
            "priorita",
            "priorità",
            "rank",
            "ordine",
        ],
        "reason": [
            "reason",
            "explanation",
            "motivation",
            "motivazione",
            "spiegazione",
        ],
    }

    for canonical_key, aliases in key_aliases.items():
        if canonical_key in normalized:
            continue
        for alias in aliases:
            if alias in normalized:
                normalized[canonical_key] = normalized[alias]
                break

    return normalized

#Permette piccole variazioni come "paziente A" o testo con id incluso
#Tutte cose possibili con llm che generano la risposta
def normalize_patient_id(value, patient_ids):
    text = normalize_text(str(value or "")).replace(" ", "_")
    for patient_id in patient_ids:
        if text == normalize_text(patient_id):
            return patient_id

    for patient_id in patient_ids:
        normalized_id = normalize_text(patient_id)
        if normalized_id in text:
            return patient_id

    return str(value or "")
#Controllo sulla spiegazione finale del medico per ritentare il triage in caso di errori
def find_triage_consistency_error(data, scenario):
    selected_patient_id = data["selected_patient_id"] #Paziente scelto
    patient_ids = [] #Costruisco lista di tutti i pazienti dello scenario
    for patient in scenario.patients:
        patient_ids.append(patient.patient_id)

    #Prendo la spiegazione llm e si normalizza tutto per controllare meglio
    explanation = normalize_text(data.get("explanation", "")) #Funzione di normalizazione per togliere 
    #Per ogni frase
    for sentence in split_text_sentences(explanation):
        #Cerca se nella frase sono indicati come urgenti altri pazienti
        if sentence_mentions_more_urgent_patient(sentence,selected_patient_id,patient_ids):
            return (
                "La spiegazione indica come piu' urgente un paziente diverso "
                "da selected_patient_id."
            )
        #Cerca se il paziente selezionato nella sua frase ha un low priority marker
        if selected_patient_id in sentence and has_lower_priority_marker(sentence):
            return (
                "La spiegazione descrive il paziente selezionato come meno urgente."
            )
        #Cerca attribuzioni cliniche sbagliate nella spiegazione del modello
        wrong_fact = find_wrong_patient_fact(sentence, scenario)
        if wrong_fact:
            return wrong_fact

    return None

#Spiegazione di FALLBACK se il modello contraddice la propria tabella
def build_consistent_triage_explanation(data):
    selected_patient_id = data["selected_patient_id"]
    first_reason = ""

    for row in data["priority_table"]:
        if row["patient_id"] == selected_patient_id:
            first_reason = row["reason"]
            break

    if first_reason:
        return (
            "La priorita' resta a "
            + selected_patient_id
            + " perche' la tabella di triage lo colloca al primo posto: "
            + first_reason
            + "."
        )

    return (
        "La priorita' resta a "
        + selected_patient_id
        + " perche' e' il paziente al primo posto nella tabella di triage."
    )

#Normalizzazione confronti testuali senza dipendere dagli accenti.
#Senza questa poteva dare errori nel controllo. Controlla la ù e ’. 
def normalize_text(text):
    return str(text).lower().replace("\u00f9", "u").replace("\u2019", "'")

#Divide la spiegazione in frasi per i controlli di coerenza
def split_text_sentences(text):
    sentences = []
    current = ""
    for char in text:
        current += char
        if char in ".;?!\n":
            clean = current.strip()
            if clean:
                sentences.append(clean)
            current = ""

    clean = current.strip()
    if clean:
        sentences.append(clean)

    return sentences #Ritorna una lista di stringhe

#True se una frase sembra indicare come urgente un paziente diverso
def sentence_mentions_more_urgent_patient(sentence, selected_patient_id, patient_ids):
    if not has_higher_priority_marker(sentence):
        return False
    if has_lower_priority_marker(sentence):
        return False
    for patient_id in patient_ids:
        if patient_id != selected_patient_id and patient_id in sentence:
            return True
    return False

#Parole che indicano urgenza o priorità
def has_higher_priority_marker(sentence):
    
    markers = [
        "piu urgente",
        "piu' urgente",
        "prioritario",
        "prioritaria",
        "massima urgenza",
    ]
    for marker in markers:
        if marker in sentence:
            return True

    return False

#Parole comuni che indicano minore priorità
def has_lower_priority_marker(sentence):
    markers = [
        "meno urgente",
        "meno prioritario",
        "meno prioritaria",
        "non prioritario",
        "non prioritaria",
    ]
    for marker in markers:
        if marker in sentence:
            return True

    return False

#Cerca falsi segni clinici nella spiegazione del modello
def find_wrong_patient_fact(sentence, scenario):
    patient_signs = {}
    all_signs = set()

    for patient in scenario.patients:
        normalized_signs = set()
        for sign in patient.signs:
            normalized = normalize_text(sign)
            normalized_signs.add(normalized)
            all_signs.add(normalized)
        patient_signs[patient.patient_id] = normalized_signs

    for patient_id, signs in patient_signs.items():
        if patient_id not in sentence:
            continue

        for sign in all_signs:
            if (
                contains_full_fact(sentence, sign)
                and sign not in signs
                and not is_negated_fact(sentence, sign)
            ):
                return (
                    "La spiegazione attribuisce a "
                    + patient_id
                    + " un segno non osservato: "
                    + sign
                    + "."
                )

    return None

#Evita falsi positivi quando il segno è citato in forma negativa
#Utilizzata dalle funzioni per trovare i falsi segni clinici
def is_negated_fact(sentence, sign):
    prefixes = [
        "non ha ",
        "senza ",
        "nessun ",
        "nessuna ",
    ]
    for prefix in prefixes:
        if contains_full_fact(sentence, prefix + sign):
            return True

    return False

#Funzione che controlla se una stringa fact è contenuta dentro una frase sentence
#Deve essere trovata come fatto completo, non come pezzo dentro un’altra parola
#Serve a evitare falsi positivi.
def contains_full_fact(sentence, fact):
    start = 0
    while True:
        index = sentence.find(fact, start)
        if index == -1:
            return False

        before_index = index - 1
        after_index = index + len(fact)

        before_ok = before_index < 0 or not sentence[before_index].isalnum()
        after_ok = after_index >= len(sentence) or not sentence[after_index].isalnum()
        if before_ok and after_ok:
            return True

        start = index + 1

#Risposta deterministica dell'esploratore quando l'LLM non funziona. FALLBACK
def make_local_clarification_answer(question, scenario):
    question_lower = question.lower()

    for patient in scenario.patients:
        area = find_area_by_id(scenario, patient.area_id)
        if patient.patient_id.lower() in question_lower:
            answer = (
                "Confermo per "
                + patient.patient_id
                + ": "
                + ", ".join(patient.signs)
                + ". Area "
                + area.area_id
                + ": "
                + ", ".join(area.observations)
                + ". "
            )
            if area.location in scenario.unsafe_cells:
                answer += "L'area risulta ancora da mettere in sicurezza. "
            answer += "Non aggiungo dati non osservati."
            return answer

    for area in scenario.areas:
        if area.area_id.lower() in question_lower:
            answer = (
                "Confermo in "
                + area.area_id
                + ": "
                + ", ".join(area.observations)
                + ". "
            )
            if area.location in scenario.unsafe_cells:
                answer += "L'area e' segnata come non sicura per l'ingresso del medico."
            else:
                answer += "L'area non e' marcata come instabile per il medico."
            return answer

    return "Non ho un nuovo dato certo. Posso confermare solo lo scenario osservato."

#Nome PDDL per una location
def location_name(location):
    return "l_" + str(location[0]) + "_" + str(location[1])
def free_cells(scenario):
    #Restituisce tutte le celle attraversabili, escludendo i blocchi (tipo stanze collassate e non attraversabili)
    cells = []
    for row in range(scenario.rows):
        for col in range(scenario.cols):
            cell = (row, col)
            if cell not in scenario.blocked_cells:
                cells.append(cell)
    return cells
#Costruisce le adiacenze tra le celle
def adjacent_pairs(scenario):
    pairs = []
    cells = free_cells(scenario)
    free = set(cells)
    directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    for cell in cells:
        for direction in directions:
            other = (cell[0] + direction[0], cell[1] + direction[1])
            if other in free:
                pairs.append((cell, other))

    return pairs

#Controlla se una coppia di celle e' un varco ostruito da macerie. First e second devono essere tuple.
def is_rubble_gate(scenario, first, second):
    return normalize_rubble(first, second) in scenario.rubble_gates
def safe_file_stem(text):
    #Rende l'id area sicuro per il nome del file PDDL generato.
    safe = ""
    for char in str(text):
        if char.isalnum() or char in ("_", "-"):
            safe += char
        else:
            safe += "_"

    return safe or "area"

#QUI SI GENERA IL PDDL DI EXPLORAZIONE
def generate_exploration_problem(scenario, start_location, target_location, output_path):
    #Genera un problema PDDL piccolo: spostare solo l'esploratore verso una zona.
    output_path = Path(output_path)

    #Oggetti PDDL: una location per ogni cella libera.
    location_names = []
    for cell in free_cells(scenario):
        location_names.append(location_name(cell))

    #link descrive l'adiacenza; clear i passaggi liberi; rubble quelli liberabili.
    link_lines = []
    clear_lines = []
    rubble_lines = []

    for pair in adjacent_pairs(scenario):
        first = pair[0]
        second = pair[1]
        link_lines.append(
            "    (link "
            + location_name(first)
            + " "
            + location_name(second)
            + ")"
        )

        if is_rubble_gate(scenario, first, second):
            rubble_lines.append(
                "    (rubble "
                + location_name(first)
                + " "
                + location_name(second)
                + ")"
            )
        else:
            clear_lines.append(
                "    (clear "
                + location_name(first)
                + " "
                + location_name(second)
                + ")"
            )

    #Il testo PDDL viene scritto come stringa per restare leggibile e ispezionabile.
    content = """(define (problem rubble_exploration_generated)
  ; Problema generato per un singolo spostamento del robot esploratore.
  ; Il goal e' raggiungere la zona indicata dal medico.
  ; L'esploratore puo' liberare varchi ostruiti, ma non mette in sicurezza aree.

  (:domain rubble_rescue)

  (:objects
    esploratore - robot
    """ + " ".join(location_names) + """ - location
  )

  (:init
    (explorer esploratore)
    (at esploratore """ + location_name(start_location) + """)
""" + "\n".join(link_lines) + """
""" + "\n".join(clear_lines) + """
""" + "\n".join(rubble_lines) + """
    (= (total-cost) 0)
  )

  (:goal
    (and
      (at esploratore """ + location_name(target_location) + """)
    )
  )

  (:metric minimize (total-cost))
)
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path

#QUI SI GENERA IL PROBLEMA PDDL FINALE
#Quello di soccorso del paziente
def generate_problem(scenario, selected_patient_id, output_path):
    output_path = Path(output_path)
    patient = find_patient_by_id(scenario, selected_patient_id)

    #Oggetti PDDL: medico, civile e tutte le celle libere.
    location_names = []
    for cell in free_cells(scenario):
        location_names.append(location_name(cell))

    #Liste separate per predicati diversi nel blocco :init.
    link_lines = []
    clear_lines = []
    rubble_lines = []
    secured_lines = []

    #Ogni adiacenza e' un collegamento fisico; non tutte sono gia' libere.
    for pair in adjacent_pairs(scenario):
        first = pair[0]
        second = pair[1]
        link_lines.append(
            "    (link "
            + location_name(first)
            + " "
            + location_name(second)
            + ")"
        )

        if is_rubble_gate(scenario, first, second):
            rubble_lines.append(
                "    (rubble "
                + location_name(first)
                + " "
                + location_name(second)
                + ")"
            )
        else:
            clear_lines.append(
                "    (clear "
                + location_name(first)
                + " "
                + location_name(second)
                + ")"
            )

    #Le celle non rischiose sono gia' sicure per il medico.
    for cell in free_cells(scenario):
        if cell not in scenario.unsafe_cells:
            secured_lines.append("    (secured " + location_name(cell) + ")")

    content = """(define (problem rubble_rescue_generated)
  ; Problema generato dal main dopo la scelta del paziente da parte del medico.
  ; Il medico puo' intervenire quando l'area del paziente e' in sicurezza.

  (:domain rubble_rescue)

  (:objects
    medico soccorritore_civile - robot
    """ + " ".join(location_names) + """ - location
  )

  (:init
    (medical medico)
    (civilian soccorritore_civile)
    (at medico """ + location_name(scenario.medic_start) + """)
    (at soccorritore_civile """ + location_name(scenario.civilian_start) + """)
""" + "\n".join(link_lines) + """
""" + "\n".join(clear_lines) + """
""" + "\n".join(rubble_lines) + """
""" + "\n".join(secured_lines) + """
    (= (total-cost) 0)
  )

  (:goal
    (and
      (secured """ + location_name(patient.location) + """)
      (at medico """ + location_name(patient.location) + """)
    )
  )

  (:metric minimize (total-cost))
)
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path

#CORPO DELLA COMUNICAZIONE
def main():
    #Fase 1: preparo percorsi, carico .env e trasformo il JSON in oggetti Python
    project_dir = Path(__file__).resolve().parent
    env_path = project_dir / ".env"
    env_loaded = load_dotenv(env_path)

    #scenario = load_scenario(project_dir / "data" / "scenario_macerie.json")
    #scenario = load_scenario(project_dir / "data" / "scenario_macerie_unreach.json")
    scenario = load_scenario(project_dir / "data" / "scenario_macerie_rubble.json")

    display = Display()

    #Fase 2: intestazione nel terminale e conferma dello scenario caricato
    display.section("Avvio intervento")
    display.system(scenario.name)
    display.system(".env caricato: " + (str(env_path) if env_loaded else "non trovato"))

    #Fase 3: costruisco i due client LLM sulla base dei paramtri .env o default
    #Ollama o local sono i casi locali o deterministico. Groq è l'unico provider remoto con API_KEY
    explorer_llm = os.getenv("EXPLORER_LLM", "ollama")
    medic_llm = os.getenv("MEDIC_LLM", "groq")
    explorer_client = create_llm_client(explorer_llm, role="explorer")
    medic_client = create_llm_client(medic_llm, role="medic")
    explorer = RescueRobot(explorer_client, "robot esploratore")
    medic = RescueRobot(medic_client, "robot medico")

    #Stampo i modelli effettivi nel setting .env
    display.system(
        "Modelli: esploratore="
        + explorer_llm
        + "/"
        + explorer_client.model
        + ", medico="
        + medic_llm
        + "/"
        + medic_client.model
    )

    #Fase 4: inizializzo lo stato dell'esplorazione
    #reports contiene le descrizioni delle zone, clarification_answers le risposte ai dubbi del medico
    display.section("Esplorazione guidata con PDDL")
    reports = []
    clarification_answers = []
    asked_questions = []
    unexplored_areas = list(scenario.areas)
    explorer_location = scenario.explorer_start
    pending_direction = None

    #Messaggio iniziale del medico: non decide ancora nulla, apre solo la comunicazione
    initial_message = medic.compose_message(
        "robot esploratore",
        "spiegare che guiderai l'esplorazione zona per zona usando PDDL",
        "Scenario: "
        + scenario.name
        + "\nPosizione iniziale esploratore: "
        + str(explorer_location)
        + "\nAree da esplorare: "
        + ", ".join([area.area_id for area in scenario.areas]),
        "Ti guidero' zona per zona. Ogni spostamento verra' pianificato con PDDL.",
    )
    say_llm_result(display, "Medico", initial_message)

    #Fase 5: ciclo di esplorazione zona per zona
    #A ogni iterazione il medico sceglie una zona, PDDL calcola lo spostamento,
    #l'esploratore descrive la zona e il medico puo' chiedere chiarimenti
    while unexplored_areas:
        #Il medico sceglie la prossima area, ma non il percorso.
        if pending_direction is None:
            direction = medic.choose_next_area(
                reports,
                unexplored_areas,
                explorer_location,
                scenario,
            )
        else:
            direction = pending_direction
            pending_direction = None
        show_llm_diagnostic(display, direction) #Se ci sono errori vengono mostrati e viene notificato utilizzo della fallback

        #Recupero l'oggetto area scelto e mostro l'ordine operativo
        area = find_area_by_id(scenario, direction.area_id)
        #Il testo è fisso, ma la decisione la prende llm
        display.say(
            "Medico (" + provider_label(direction) + ")",
            "Dirigiti verso "
            + area.area_id
            + ". "
            + direction.reason,
        )

        #Genero un problema PDDL dedicato allo spostamento dell'explorer
        display.section("Planning esploratore: " + area.area_id)
        exploration_problem_path = generate_exploration_problem(
            scenario,
            explorer_location,
            area.location,
            project_dir
            / "output"
            / ("problem_exploration_" + safe_file_stem(area.area_id) + ".pddl"),
        )
        display.system("Problema esplorazione: " + str(exploration_problem_path))

        #Se la zona scelta coincide con la posizione attuale, non serve invocare il planner
        if explorer_location == area.location:
            display.system("L'esploratore e' gia' nella zona indicata: nessuno spostamento necessario.")
        else:
            try:
                #Fast Downward calcola solo il percorso verso la zona scelta
                #Lancio run_planner che ho importato
                plan = run_planner(
                    project_dir / "pddl" / "domain_rescue.pddl",
                    exploration_problem_path,
                    project_dir,
                )
            except PlannerError as exc:
                #Se il percorso non esiste, l'esploratore comunica il blocco
                blocked_message = explorer.compose_message(
                    "robot medico",
                    "comunicare che non riesci a raggiungere la zona indicata",
                    "Area richiesta: "
                    + area.area_id
                    + "\nPartenza: "
                    + str(explorer_location)
                    + "\nDestinazione: "
                    + str(area.location)
                    + "\nIl planner PDDL non ha prodotto un percorso.\nErrore: "
                    + str(exc),
                    "Non riesco a raggiungere "
                    + area.area_id
                    + ": il planner PDDL non ha trovato un percorso. Attendo una tua decisione.",
                )
                say_llm_result(display, "Esploratore -> Medico", blocked_message)

                unexplored_areas = [
                    candidate
                    for candidate in unexplored_areas
                    if candidate.area_id != area.area_id
                ]
                available_areas = unexplored_areas
                flow_decision = medic.decide_after_unreachable_area(
                    reports,
                    area,
                    available_areas,
                    explorer_location,
                    scenario,
                )
                show_llm_diagnostic(display, flow_decision)
                display.say(
                    "Medico (" + provider_label(flow_decision) + ")",
                    flow_decision.reason,
                )

                if (
                    flow_decision.action == "continue_exploration"
                    and available_areas
                ):
                    unexplored_areas = available_areas
                    pending_direction = AreaDirection(
                        flow_decision.next_area_id,
                        flow_decision.reason,
                        flow_decision.provider,
                        flow_decision.model,
                        flow_decision.used_fallback,
                        flow_decision.error,
                    )
                    continue

                break

            #Stampa del piano di movimento dell'esploratore
            display.system("Planner usato: " + plan.source)
            display.system("Costo percorso esploratore: " + str(plan.cost))
            display.line("\nPiano esploratore:")
            for index, action in enumerate(plan.actions, start=1):
                display.line(str(index).zfill(2) + ". " + action)

        #Aggiorno la posizione logica dell'esploratore dopo il piano
        explorer_location = area.location

        #L'esploratore conferma l'arrivo prima di mandare il report vero e proprio
        entry_message = explorer.compose_message(
            "robot medico",
            "comunicare che hai raggiunto l'area indicata e che invierai un report",
            "Area: "
            + area.area_id
            + "\nPosizione: "
            + str(area.location),
            "Ho raggiunto " + area.area_id + ". Raccolgo osservazioni.",
        )
        say_llm_result(display, "Esploratore", entry_message)

        #Report dall'osservazione area, qui entrano paziente e osservazioni ambientali
        report = explorer.describe_area(area)
        reports.append(report)

        say_llm_result(display, "Esploratore -> Medico", report)
        #Il medico puo' chiedere fino a tre chiarimenti sulla singola area appena vista.
        area_clarification_request = medic.request_area_clarifications(
            area,
            report,
            scenario,
        )
        show_llm_diagnostic(display, area_clarification_request) #Serve per verificare fallback
        #Ogni domanda viene salvata per alimentare il triage finale
        if area_clarification_request.questions:
            for question in area_clarification_request.questions:
                asked_questions.append(question)
                display.say("Medico -> Esploratore", question)
                answer = explorer.answer_clarification(question, scenario)
                say_llm_result(display, "Esploratore -> Medico", answer)
                clarification_answers.append(
                    {
                        "question": question,
                        "answer": answer.text,
                    }
                )
        else:
            #Se non servono chiarimenti, il medico conferma ricezione e si prosegue.
            ack_message = medic.compose_message(
                "robot esploratore",
                "confermare ricezione del report e autorizzare il prossimo spostamento",
                "Report ricevuto da "
                + area.area_id
                + ":\n"
                + report.text,
                "Ricevuto. Non ho chiarimenti su questa area, puoi restare pronto al prossimo spostamento.",
            )
            say_llm_result(display, "Medico", ack_message)

        #Tolgo dall'elenco la zona appena esplorata.
        unexplored_areas = [
            candidate
            for candidate in unexplored_areas
            if candidate.area_id != area.area_id
        ]

    #Fase 6: chiusura esplorazione
    #A questo punto l'esplorazione è completa oppure il medico ha deciso di chiuderla
    reported_area_ids = []
    for report in reports:
        reported_area_ids.append(report.area_id)
    missing_area_ids = []
    for area in scenario.areas:
        if area.area_id not in reported_area_ids:
            missing_area_ids.append(area.area_id)

    #Intent e fallback di compose sotto
    closing_intent = "comunicare che tutte le zone richieste sono state esplorate"
    closing_fallback = "Ho completato l'esplorazione di tutte le zone richieste e ho trasmesso i report."
    if missing_area_ids:
        #Dico al medico che non ho raggiunto tutte le zone ma che ho inviato tutti i report.
        #Questo va come testo del compose dell'explorer sotto.
        closing_intent = (
            "comunicare che l'esplorazione si chiude con alcune zone non "
            "raggiunte e che hai trasmesso i report disponibili"
        )
        #Preparo la fallback
        closing_fallback = (
            "Ho concluso l'esplorazione con i report disponibili. Zone senza "
            "report: "
            + ", ".join(missing_area_ids)
            + "."
        )

    all_explored_message = explorer.compose_message(
        "robot medico",
        closing_intent,
        "Report raccolti: "
        + str(len(reports))
        + "\nZone senza report: "
        + (", ".join(missing_area_ids) if missing_area_ids else "nessuna"),
        closing_fallback,
    )
    say_llm_result(display, "Esploratore -> Medico", all_explored_message)

    #Il medico annuncia che passa dalla raccolta dati alla decisione.
    final_triage_message = medic.compose_message(
        "robot esploratore",
        "comunicare che ora userai report e risposte per la decisione finale",
        "Chiarimenti ricevuti: " + str(len(clarification_answers)),
        "Ho ricevuto i chiarimenti. Ora assegno la priorita' finale.",
    )
    say_llm_result(display, "Medico", final_triage_message)

    #Fase 7: triage finale
    #La decisione usa report, chiarimenti e validatori per produrre una tabella ordinata
    decision = medic.triage(
        reports,
        clarification_answers,
        scenario,
        asked_questions,
    )

    #Stampa della decisione e della tabella completa di priorità
    display.section("Decisione medica")
    display.say(
        "Medico (" + provider_label(decision) + ")",
        "Paziente prioritario: "
        + decision.selected_patient_id
        + ". "
        + decision.explanation,
    )
    show_llm_diagnostic(display, decision) #In caso di fallback

    display.line("\nPriorita':")
    if decision.priority_table:
        for row in decision.priority_table:
            display.line(
                "  "
                + str(row["priority"])
                + ". "
                + row["patient_id"]
                + " - "
                + row["reason"]
            )
    else:
        display.line("  Nessun paziente osservato nelle aree raggiunte.")

    if not decision.selected_patient_id:
        display.system(
            "Nessun paziente selezionabile: non genero il piano operativo finale."
        )
        return

    #Fase 8: genero il problema PDDL finale verso il paziente scelto
    display.section("Aggiornamento PDDL")
    display.say(
        "Sistema",
        "Aggiorno il problema PDDL con varchi, macerie e sicurezza dell'area.",
    )
    problem_path = generate_problem(
        scenario,
        decision.selected_patient_id,
        project_dir / "output" / "problem_rescue_generated.pddl",
    )
    display.system("Problema aggiornato: " + str(problem_path))

    #Fase 9: planning operativo finale
    #Il piano include eventuale messa in sicurezza prima dell'ingresso del medico
    display.section("Planning con Fast Downward")
    try:
        fast_downward_path = project_dir / "fast-downward-24.06.1" / "fast-downward.py"
        display.system("Fast Downward: " + str(fast_downward_path))
        display.say("Sistema", "Invio dominio e problema al planner.")
        plan = run_planner(
            project_dir / "pddl" / "domain_rescue.pddl",
            problem_path,
            project_dir,
        )
    except PlannerError as exc:
        #Errore esplicito: senza piano finale non viene simulato alcun intervento.
        display.system("Errore planning:")
        display.line(str(exc))
        return

    #Output conclusivo del planner: fonte, costo e azioni ordinate
    display.system("Planner usato: " + plan.source)
    display.system("Costo stimato: " + str(plan.cost))
    display.line("\nPiano generato:")
    for index, action in enumerate(plan.actions, start=1):
        display.line(str(index).zfill(2) + ". " + action)


if __name__ == "__main__":
    main()
