import os
import re
from copy import deepcopy
from pathlib import Path


#Pattern usato per convertire una location PDDL del tipo l_riga_colonna
#nella corrispondente coordinata della griglia Python.
LOCATION_PATTERN = re.compile(r"^l_(\d+)_(\d+)$", re.IGNORECASE)


#Normalizza un varco tra due celle, così (A, B) e (B, A) hanno la stessa chiave.
def _normalize_gate(first, second):
    return (first, second) if first <= second else (second, first)


#Estrae il nome e gli argomenti da una singola azione prodotta dal planner.
#Rimuove parentesi e commenti PDDL prima di separare i token.
def _parse_action(raw_action):
    text = str(raw_action).split(";", 1)[0].strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    tokens = text.split()
    return (tokens[0].lower(), tokens[1:]) if tokens else ("", [])


#Converte una location PDDL in una tupla (riga, colonna).
#Se il nome non rispetta il formato previsto, segnala subito il plan non valido.
def _parse_location(token):
    match = LOCATION_PATTERN.fullmatch(str(token).strip())
    if match is None:
        raise ValueError("Location PDDL non riconosciuta: " + str(token))
    return int(match.group(1)), int(match.group(2))


class PlanAnimator:
    #Associa i nomi degli oggetti PDDL ai nomi interni usati nell'animazione.
    ACTOR_BY_PDDL_NAME = {
        "esploratore": "explorer",
        "medico": "medic",
        "soccorritore_civile": "civilian",
    }
    #Etichette leggibili mostrate sotto le icone degli attori.
    ACTOR_LABELS = {
        "explorer": "Esploratore",
        "medic": "Medico",
        "civilian": "Soccorritore",
    }
    #Colori usati dai pallini quando una specifica icona PNG non è disponibile.
    ACTOR_COLORS = {
        "explorer": "#f4b400",
        "medic": "#1a73e8",
        "civilian": "#34a853",
    }
    #Nomi dei file PNG cercati nella cartella src/asset.
    #Ogni elemento può comunque essere disegnato tramite il proprio fallback.
    ICON_FILES = {
        "free_cell": "free_cell.png",
        "blocked_cell": "blocked_cell.png",
        "rubble": "rubblegates.png",
        "explorer": "explorer.png",
        "medic": "medic.png",
        "civilian": "civil.png",
        "patient": "patient.png",
    }

    #Inizializza lo stato persistente dell'animazione a partire dallo scenario.
    def __init__(self, scenario, project_dir, step_delay_ms=700):
        self.scenario = scenario                    #Scenario completo caricato dal JSON
        self.project_dir = Path(project_dir)        #Cartella principale del progetto
        self.icon_dir = self.project_dir / "src" / "asset" #Cartella delle icone opzionali
        self.step_delay_ms = int(step_delay_ms)     #Intervallo tra due azioni in autoplay
        self.enabled = os.getenv("RESCUE_ANIMATION", "1") != "0" #Permette di disabilitare la GUI nei test

        #Le posizioni sono inizializzate dai punti di partenza dello scenario.
        #A differenza delle rubble, verranno mantenute tra un plan e il successivo.
        self.positions = {
            "explorer": tuple(scenario.explorer_start),
            "medic": tuple(scenario.medic_start),
            "civilian": tuple(scenario.civilian_start),
        }
        # Le posizioni persistono tra i plan; le macerie, invece, ripartono sempre
        # dalla configurazione completa dichiarata nello scenario.
        self.initial_rubble_gates = set(scenario.rubble_gates)
        self.rubble_gates = set(self.initial_rubble_gates)

        #All'avvio sono sicure tutte le celle libere non presenti in unsafe_cells.
        self.secured_cells = {
            (row, col)
            for row in range(scenario.rows)
            for col in range(scenario.cols)
            if (row, col) not in scenario.blocked_cells
            and (row, col) not in scenario.unsafe_cells
        }
        self.last_error = None #Eventuale errore grafico, senza interrompere il planning

    #Converte il plan in una sequenza di frame e avvia la finestra Tk.
    #Lo stato logico viene aggiornato prima della visualizzazione, quindi chiudere
    #la finestra in anticipo non modifica il risultato finale del plan.
    def animate_plan(self, plan, title):
        #Ogni nuovo plan rilegge tutte le macerie originali dello scenario.
        #Le posizioni degli attori, invece, restano quelle raggiunte in precedenza.
        self.rubble_gates = set(self.initial_rubble_gates)

        #Il primo frame rappresenta sempre lo stato prima della prima azione.
        frames = [self._snapshot("Stato iniziale")]
        for action in plan.actions:
            #Applico un'azione alla volta e salvo lo stato ottenuto.
            self._apply_action(action)
            frames.append(self._snapshot(action))

        #Nei test automatici aggiorno lo stato senza aprire alcuna finestra.
        if not self.enabled:
            return

        try:
            #La finestra è modale rispetto al flusso: il main continua dopo la chiusura.
            window = _AnimationWindow(self, frames, title)
            window.run()
        except Exception as exc:
            # Una GUI indisponibile (per esempio in CI/headless) non blocca il soccorso.
            self.last_error = str(exc)

    #Crea una copia indipendente dello stato corrente da usare come frame.
    #Le copie evitano che le azioni successive modifichino i frame già creati.
    def _snapshot(self, action):
        return {
            "action": action,
            "positions": deepcopy(self.positions),
            "rubble_gates": set(self.rubble_gates),
            "secured_cells": set(self.secured_cells),
        }

    #Aggiorna lo stato visuale interpretando le azioni definite nel dominio PDDL.
    def _apply_action(self, raw_action):
        action, args = _parse_action(raw_action)

        #Le azioni move-* spostano l'attore dalla cella corrente alla destinazione.
        if action.startswith("move-") and len(args) >= 3:
            actor = self.ACTOR_BY_PDDL_NAME.get(args[0].lower())
            if actor is not None:
                self.positions[actor] = _parse_location(args[2])
            return

        #La rimozione elimina il varco ostruito solo nei frame del plan corrente.
        if action.startswith("remove-rubble-") and len(args) >= 3:
            first = _parse_location(args[1])
            second = _parse_location(args[2])
            self.rubble_gates.discard(_normalize_gate(first, second))
            return

        #La messa in sicurezza registra la cella raggiunta dal soccorritore civile.
        if action == "secure-area" and len(args) >= 2:
            self.secured_cells.add(_parse_location(args[1]))


class _AnimationWindow:
    #Costruisce una nuova finestra per la sequenza di frame ricevuta.
    def __init__(self, animator, frames, title):
        #Tkinter viene importato qui per permettere l'uso del planner anche in
        #ambienti senza interfaccia grafica quando l'animazione è disabilitata.
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk                         #Modulo Tk usato per widget e immagini base
        self.ttk = ttk                       #Widget grafici con stile nativo
        self.animator = animator             #Stato condiviso e configurazione scenario
        self.frames = frames                 #Stati consecutivi prodotti dal plan
        self.index = 0                       #Indice del frame attualmente mostrato
        self.playing = True                  #True mentre l'autoplay è attivo
        self.after_id = None                 #Id callback Tk usato per annullare l'autoplay
        self.images = {}                     #Immagini originali caricate da disco
        self.scaled_images = {}              #Cache delle immagini ridimensionate/ruotate
        self.pil_sources = {}                #Sorgenti Pillow con canale alpha
        self.image_tk = None                 #Riferimento a ImageTk se Pillow è disponibile

        #Configurazione della finestra principale dell'animazione.
        self.root = tk.Tk()
        self.root.title("Piano PDDL - " + title)
        self.root.geometry("980x720")
        self.root.minsize(680, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        #Variabili Tk collegate a testo informativo e slider temporale.
        self.info_var = tk.StringVar()
        self.step_var = tk.IntVar(value=0)

        #Costruisco i widget, tento il caricamento delle icone e mostro il frame zero.
        self._build_ui()
        self._load_icons()
        self.draw()
        self.root.after(350, self._play)

    #Avvia il ciclo eventi Tk e mantiene aperta la finestra.
    def run(self):
        self.root.mainloop()

    #Costruisce intestazione, controlli, canvas della griglia e lista delle azioni.
    def _build_ui(self):
        #Parte superiore con descrizione del passo corrente.
        top = self.ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        self.ttk.Label(top, textvariable=self.info_var, font=("Segoe UI", 11, "bold")).pack(anchor="w")

        #Pulsanti per navigazione, autoplay, chiusura e slider dei frame.
        controls = self.ttk.Frame(top)
        controls.pack(fill="x", pady=(8, 0))
        self.ttk.Button(controls, text="<<", command=self.previous_step).pack(side="left")
        self.ttk.Button(controls, text="Play / Pausa", command=self.toggle_play).pack(side="left", padx=6)
        self.ttk.Button(controls, text=">>", command=self.next_step).pack(side="left")
        self.ttk.Button(controls, text="Continua", command=self.close).pack(side="right")
        self.slider = self.ttk.Scale(
            controls,
            from_=0,
            to=max(0, len(self.frames) - 1),
            variable=self.step_var,
            command=self.on_slider,
        )
        self.slider.pack(side="left", fill="x", expand=True, padx=12)

        #Parte centrale: griglia animata a sinistra e plan testuale a destra.
        main = self.ttk.Frame(self.root, padding=(10, 0, 10, 10))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        self.canvas = self.tk.Canvas(main, bg="white", highlightthickness=1, highlightbackground="#777777")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.action_list = self.tk.Listbox(main, width=46)
        self.action_list.grid(row=0, column=1, sticky="ns", padx=(10, 0))

        #Inserisco nella lista lo stato iniziale e tutte le azioni del plan.
        for number, frame in enumerate(self.frames):
            self.action_list.insert("end", "[" + str(number) + "] " + frame["action"])

        #Ridisegno la griglia quando la finestra cambia dimensione.
        self.canvas.bind("<Configure>", lambda _event: self.draw())

    #Carica tutte le icone PNG disponibili; ogni errore abilita il fallback
    #geometrico solo per l'elemento che non è stato inizializzato.
    def _load_icons(self):
        # Il tentativo PNG avviene sempre prima del fallback geometrico.
        try:
            #Pillow permette ridimensionamento, trasparenza e rotazione delle rubble.
            from PIL import Image, ImageTk

            self.pil_image = Image
            self.image_tk = ImageTk
        except ImportError:
            #Tk PhotoImage resta disponibile anche se Pillow non è installato.
            self.pil_image = None

        for kind, filename in self.animator.ICON_FILES.items():
            path = self.animator.icon_dir / filename
            try:
                if self.pil_image is not None:
                    #Converto sempre in RGBA per mantenere il canale trasparente.
                    source = self.pil_image.open(path).convert("RGBA")
                    self.pil_sources[kind] = source
                    self.images[kind] = self.image_tk.PhotoImage(source)
                else:
                    self.images[kind] = self.tk.PhotoImage(file=str(path))
            except Exception:
                #None segnala ai metodi di disegno di usare rettangoli, linee o pallini.
                self.images[kind] = None

    #Restituisce una versione dell'icona adatta alla dimensione della cella.
    #Le versioni elaborate vengono memorizzate per non ricrearle a ogni frame.
    def _image_for(self, kind, max_size, rotate_90=False):
        image = self.images[kind]
        if image is None:
            return None
        max_size = max(1, int(max_size))

        if kind in self.pil_sources:
            #La chiave distingue dimensione e orientamento della stessa immagine.
            key = (kind, max_size, rotate_90)
            if key not in self.scaled_images:
                source = self.pil_sources[kind]
                if rotate_90:
                    #L'asset rubble è verticale: lo ruoto per gli edge orizzontali.
                    source = source.rotate(90, expand=True)
                resized = source.copy()
                resized.thumbnail(
                    (max_size, max_size),
                    self.pil_image.Resampling.LANCZOS,
                )
                self.scaled_images[key] = self.image_tk.PhotoImage(resized)
            return self.scaled_images[key]

        # Senza Pillow posso ridimensionare le PNG, ma non ruotarle conservando
        # correttamente il canale alpha: in quel caso uso la linea rossa fallback.
        if rotate_90:
            return None

        #PhotoImage supporta un ridimensionamento intero tramite subsample.
        largest_side = max(image.width(), image.height())
        factor = max(1, (largest_side + max_size - 1) // max_size)
        key = (kind, factor, False)
        if key not in self.scaled_images:
            self.scaled_images[key] = image.subsample(factor, factor)
        return self.scaled_images[key]

    #Ferma l'autoplay, annulla la callback pendente e chiude la finestra.
    def close(self):
        self.playing = False
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
        self.root.destroy()

    #Porta l'animazione al frame indicato dallo slider.
    def on_slider(self, value):
        self.index = int(float(value))
        self.draw()

    #Mostra il frame precedente senza scendere sotto lo stato iniziale.
    def previous_step(self):
        self.index = max(0, self.index - 1)
        self.step_var.set(self.index)
        self.draw()

    #Mostra il frame successivo senza superare l'ultima azione.
    def next_step(self):
        self.index = min(len(self.frames) - 1, self.index + 1)
        self.step_var.set(self.index)
        self.draw()

    #Alterna riproduzione e pausa; se il plan è terminato riparte dall'inizio.
    def toggle_play(self):
        self.playing = not self.playing
        if self.playing:
            if self.index >= len(self.frames) - 1:
                self.index = 0
                self.step_var.set(0)
            self._play()

    #Avanza automaticamente di un frame usando il timer non bloccante di Tk.
    def _play(self):
        if not self.playing:
            return
        if self.index >= len(self.frames) - 1:
            self.playing = False
            return
        self.next_step()
        self.after_id = self.root.after(self.animator.step_delay_ms, self._play)

    #Ridisegna completamente griglia ed entità per il frame selezionato.
    def draw(self):
        frame = self.frames[self.index]

        #Aggiorno descrizione, selezione nella lista e pulisco il canvas precedente.
        self.info_var.set(
            "Passo " + str(self.index) + "/" + str(len(self.frames) - 1) + " | " + frame["action"]
        )
        self.action_list.selection_clear(0, "end")
        self.action_list.selection_set(self.index)
        self.action_list.see(self.index)
        self.canvas.delete("all")

        #Calcolo la cella più grande che entra nel canvas mantenendo la griglia centrata.
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        rows = self.animator.scenario.rows
        cols = self.animator.scenario.cols
        padding = 35
        cell = max(24, int(min((width - 2 * padding) / cols, (height - 2 * padding) / rows)))
        left = (width - cell * cols) // 2
        top = (height - cell * rows) // 2

        #Disegno prima il fondo: celle bianche libere e celle nere inaccessibili.
        for row in range(rows):
            for col in range(cols):
                blocked = (row, col) in self.animator.scenario.blocked_cells
                self._draw_cell(row, col, blocked, left, top, cell)

        #Le rubble vengono disegnate sopra la griglia, sull'edge condiviso.
        for first, second in frame["rubble_gates"]:
            self._draw_rubble(first, second, left, top, cell)

        #Raggruppo pazienti e attori per cella, così posso separarli con offset
        #quando più elementi occupano contemporaneamente la stessa posizione.
        by_cell = {}
        for patient in self.animator.scenario.patients:
            by_cell.setdefault(patient.location, []).append(("patient", patient.patient_id))
        for actor, position in frame["positions"].items():
            by_cell.setdefault(position, []).append(("actor", actor))
        for position, entities in by_cell.items():
            entities.sort()
            offsets = self._entity_offsets(len(entities), cell)
            for entity, offset in zip(entities, offsets):
                kind, name = entity
                if kind == "patient":
                    self._draw_patient(name, position, offset, left, top, cell)
                else:
                    self._draw_actor(name, position, offset, left, top, cell)

    #Disegna una singola cella usando prima la PNG e poi il colore fallback.
    def _draw_cell(self, row, col, blocked, left, top, cell):
        x1 = left + col * cell
        y1 = top + row * cell
        image = self._image_for("blocked_cell" if blocked else "free_cell", cell)
        fill = "black" if blocked else "white"
        self.canvas.create_rectangle(x1, y1, x1 + cell, y1 + cell, fill=fill, outline="#777777")
        if image is not None:
            self.canvas.create_image(x1 + cell / 2, y1 + cell / 2, image=image)

    #Disegna una rubble esattamente sul bordo condiviso da due celle adiacenti.
    def _draw_rubble(self, first, second, left, top, cell):
        row_delta = second[0] - first[0]
        col_delta = second[1] - first[1]

        if row_delta != 0 and col_delta == 0:
            #Celle una sopra l'altra: evidenzio il bordo orizzontale condiviso
            border_row = max(first[0], second[0])
            x1 = left + first[1] * cell
            y1 = top + border_row * cell
            x2 = x1 + cell
            y2 = y1
        elif col_delta != 0 and row_delta == 0:
            #Celle affiancate: evidenzio il bordo verticale condiviso
            border_col = max(first[1], second[1])
            x1 = left + border_col * cell
            y1 = top + first[0] * cell
            x2 = x1
            y2 = y1 + cell
        else:
            return

        #L'immagine di partenza è verticale: per un bordo orizzontale la ruoto.
        image = self._image_for(
            "rubble",
            int(cell * 0.65),
            rotate_90=row_delta != 0,
        )
        if image is not None:
            self.canvas.create_image((x1 + x2) / 2, (y1 + y2) / 2, image=image)
        else:
            #Fallback: una linea rossa spessa lungo tutto l'edge ostruito.
            self.canvas.create_line(x1, y1, x2, y2, fill="#e00000", width=max(4, cell // 12))

    #Disegna esploratore, medico o soccorritore nella posizione del frame.
    def _draw_actor(self, actor, position, offset, left, top, cell):
        x = left + position[1] * cell + cell / 2 + offset[0]
        y = top + position[0] * cell + cell / 2 + offset[1]
        image = self._image_for(actor, int(cell * 0.65))
        label = self.animator.ACTOR_LABELS[actor]
        if image is not None:
            #Se la PNG esiste, mostro anche l'etichetta leggibile sotto l'icona.
            self.canvas.create_image(x, y, image=image)
            self.canvas.create_text(x, y + cell * 0.35, text=label, font=("Segoe UI", 8, "bold"))
            return

        #Fallback dell'attore: pallino colorato con l'iniziale del ruolo.
        radius = max(7, cell * 0.14)
        self.canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            fill=self.animator.ACTOR_COLORS[actor],
            outline="black",
            width=2,
        )
        self.canvas.create_text(x, y, text=label[0], fill="white", font=("Segoe UI", 9, "bold"))

    #Disegna un paziente nella location assegnata dall'area dello scenario.
    def _draw_patient(self, patient_id, position, offset, left, top, cell):
        x = left + position[1] * cell + cell / 2 + offset[0]
        y = top + position[0] * cell + cell / 2 + offset[1]
        image = self._image_for("patient", int(cell * 0.65))
        if image is not None:
            #L'id permette di distinguere i pazienti direttamente sulla griglia.
            self.canvas.create_image(x, y, image=image)
            self.canvas.create_text(
                x,
                y + cell * 0.35,
                text=patient_id,
                fill="#8b0000",
                font=("Segoe UI", 8, "bold"),
            )
            return

        #Fallback del paziente: simbolo rosso con croce bianca e identificativo.
        radius = max(7, cell * 0.14)
        self.canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            fill="#d93025",
            outline="#7f0000",
            width=2,
        )
        cross = radius * 0.55
        self.canvas.create_line(x - cross, y, x + cross, y, fill="white", width=2)
        self.canvas.create_line(x, y - cross, x, y + cross, fill="white", width=2)
        self.canvas.create_text(
            x,
            y + radius + 9,
            text=patient_id,
            fill="#8b0000",
            font=("Segoe UI", 7, "bold"),
        )

    #Calcola piccoli scostamenti dal centro quando una cella contiene più entità.
    @staticmethod
    def _entity_offsets(count, cell):
        step = cell * 0.2
        return [
            (0, 0),
            (-step, -step),
            (step, -step),
            (-step, step),
            (step, step),
        ][:count]
