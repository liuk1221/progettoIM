# RescueRobotsEarthquake

Simulazione di recupero sotto macerie dopo un terremoto.

Ci sono tre passaggi fontamentali:

- **Esplorazione guidata dal medico**: il medico sceglie una zona alla volta, manda avanti il robot esploratore e riceve report in linguaggio naturale usando Ollama (vari modelli) o Groq. C'è anche una fallback locale per ogni chiamata in caso di problemi.
- **Spostamenti PDDL dell'esploratore**: ogni movimento tra zone viene pianificato con Fast Downward. L'esploratore puo' liberare i varchi ostruiti da macerie (`rubble`) e poi attraversarli; se il planner non trova comunque un percorso, l'esploratore lo comunica al medico. Il medico decide se chiudere l'esplorazione e passare al triage finale oppure continuare verso un'altra location.
- **Scelta del paziente prioritario**: dopo l'esplorazione completa, o dopo una chiusura anticipata, il medico usa solo report e chiarimenti delle aree raggiunte per decidere il paziente piu' urgente. I pazienti in stanze non raggiunte non entrano nella tabella di priorita'.
- **Piano di soccorso PDDL**: dopo la scelta, Fast Downward genera un piano operativo con robot medico e soccorritore civile.

Ci sono tre ruoli distinti: il **medico** guida la ricognizione e
decide il triage, il **robot esploratore** osserva e dialoga con il medico, e
il **soccorritore civile** compare nella parte PDDL finale come attore fisico
che libera varchi e stabilizza aree prima dell'arrivo del medico. Anche
l'esploratore puo' liberare varchi durante la fase di ricognizione.

Il planner non decide quale paziente aiutare. Durante l'esplorazione riceve dal
medico solo la prossima zona da raggiungere e calcola lo spostamento
dell'esploratore. Nel dominio PDDL `clear` indica un passaggio attraversabile: 
vale per esploratore, medico e soccorritore civile.
Dopo il triage il planner riceve la scelta del paziente e pianifica come
rendere possibile l'arrivo del robot medico. Il goal finale non e' solo
raggiungere una cella: l'area del paziente deve essere messa in sicurezza.
Una volta sicura, il medico puo' intervenire. Il piano puo' quindi includere
rimozione di macerie, apertura di varchi e stabilizzazione dell'area da parte
del soccorritore civile. Durante l'esplorazione, invece, la rimozione di
macerie puo' essere pianificata direttamente per l'esploratore.

## Struttura

La logica del progetto è concentrata nel `main.py`: caricamento dello
scenario, dialogo tra robot via LLM, direzione della prossima area, domande di
chiarimento, triage finale validato, fallback locale e generazione dei
problemi PDDL. Restano separati solo i file che parlano con sistemi esterni:
`src/llm_clients.py` per gli LLM e `src/planner.py` per il planner Fast Downward.

Le regole nel file `data/scenario_macerie.json`, dentro `triage_rules`, vengono
usate dal fallback locale. Le risposte alle domande del medico entrano nello
score locale come conferme dei dati osservati e vengono passate al prompt del
triage finale quando si usa un LLM. Il triage finale viene costruito su una
vista filtrata dello scenario: contiene solo le aree per cui l'esploratore ha
prodotto un report, quindi il medico non classifica pazienti mai raggiunti.

Lo scenario predefinito e' abbastanza piccolo: griglia 4x4, un ostacolo
strutturale e tre pazienti con criticita' vicine (due identiche). La scelta di triage non e'
basata su un singolo segno ovvio, ma sul bilanciamento tra respirazione,
sanguinamento, stato neurologico, polso e rischio ambientale.

## Installazione

Il file `.env` deve stare nella cartella `progettoIM` e contenere la chiave
Groq solo se vuoi usare il provider remoto:

```text
GROQ_API_KEY=...
```

Puoi scegliere quale provider usare per ogni ruolo. `local` usa i dati dello
scenario senza chiamare API esterne. `ollama` usa modelli locali. `groq`, come detto, e'
l'unico provider non locale:

```text
EXPLORER_LLM=local
MEDIC_LLM=local
```

Puoi usare provider diversi per esploratore e medico:

```text
EXPLORER_LLM=ollama
MEDIC_LLM=groq
```

Per usare modelli locali con Ollama:

```bash
ollama pull llama3.2:3b
ollama pull qwen2.5:3b
ollama pull gemma3:4b
ollama pull mistral:7b
```

Poi configura i due robot nel `.env`:

```text
EXPLORER_LLM=ollama
MEDIC_LLM=ollama
OLLAMA_BASE_URL=http://localhost:11434
EXPLORER_OLLAMA_MODEL=gemma3:4b
MEDIC_OLLAMA_MODEL=mistral:7b
```

Poi, dalla cartella `progettoIM`:

```bash
pip install -r requirements.txt
python main.py
```

Se le librerie LLM non sono installate, manca la connessione o un modello produce
una risposta non valida, il programma usa un fallback deterministico. In questo
modo si puo' provare il flusso anche senza llm.

Per forzare il fallback senza consumare chiamate API c'è un apposito parametro:

```bash
$env:RESCUE_OFFLINE="1"
python main.py
```

## Visualizzazione della comunicazione

Quando si lancia `python main.py`, la comunicazione tra robot esploratore e robot
medico viene mostrata come dialogo in terminale tramite sleep tra i caratteri.

Per una demo senza questo effetto (magari per test come `test.py`):

```bash
$env:RESCUE_FAST_UI="1"
python main.py
```

Per regolare manualmente la velocita':

```text
RESCUE_CHAR_DELAY=0.006
RESCUE_MESSAGE_DELAY=0.55
```

## Planner

Il solver PDDL usato dalla demo e' Fast Downward:

```text
fast-downward-24.06.1/fast-downward.py
```

Il modulo `src/planner.py` genera il comando sia per i problemi di esplorazione
zona per zona, sia per il problema finale di soccorso:

```bash
python fast-downward-24.06.1/fast-downward.py --alias seq-opt-lmcut pddl/domain_rescue.pddl output/problem_rescue_generated.pddl
```

Se Fast Downward non viene trovato o non produce `sas_plan`, il programma mostra
un errore esplicito. Nella fase di esplorazione questo viene comunicato come
zona non raggiungibile dall'esploratore: il medico puo' terminare la ricognizione
e arrivare comunque alla decisione finale, oppure puo' scegliere un'altra
location non ancora esplorata.

## Flusso Completo del MAIN

1. Caricamento dello scenario sotto macerie.
2. Il medico comunica che guidera' l'esplorazione zona per zona.
3. Il medico sceglie la prossima area da esplorare.
4. Viene generato un problema PDDL per spostare l'esploratore verso quell'area.
5. Se il planner non trova un percorso, l'esploratore lo comunica al medico.
6. Il medico decide se terminare l'esplorazione o continuare verso un'altra location raggiungibile.
7. Se il percorso esiste, l'esploratore raggiunge la zona e produce il report.
8. Il medico fa eventuali domande di chiarimento su quella zona.
9. Quando tutte le zone sono state esplorate, o quando il medico chiude la ricognizione, il medico sceglie il paziente prioritario.
10. Viene generato un problema PDDL finale con goal su paziente e sicurezza dell'area.


## TEST

E' presente uno script di test chiamato `test.py` che chiama il main con tutte le coppie possibile del provider
locale Ollama e salva tutte le chat in una cartella dedicata.

Esistono alcuni parametri per test:

```bash
python test.py --skip-existing
```
```bash
python test.py --list
```
```bash
python test.py
```
