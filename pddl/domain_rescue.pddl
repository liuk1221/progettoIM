(define (domain rubble_rescue)
  ; Dominio PDDL per il recupero sotto macerie.
  ; La priorita' del paziente viene decisa prima dal robot medico.
  ; Il planner deve poi coordinare medico e soccorritore civile.

  ; :strips abilita precondizioni/effetti classici.
  ; :typing permette di distinguere robot e location.
  ; :action-costs permette di sommare il costo delle azioni.
  (:requirements :strips :typing :action-costs)

  (:types
    robot location
  )

  ; (at ...) dice dove si trova un robot.
  ; (explorer ...) identifica il robot esploratore.
  ; (medical ...) identifica il robot medico.
  ; (civilian ...) identifica il soccorritore civile.
  ; (link ...) dice quali celle sono fisicamente collegate.
  ; (clear ...) dice quali collegamenti sono gia' attraversabili.
  ; (rubble ...) dice quali collegamenti sono ostruiti da macerie.
  ; (secured ...) dice quali celle sono sicure per il medico.
  (:predicates
    (at ?r - robot ?l - location)
    (explorer ?r - robot)
    (medical ?r - robot)
    (civilian ?r - robot)
    (link ?from - location ?to - location)
    (clear ?from - location ?to - location)
    (rubble ?from - location ?to - location)
    (secured ?l - location)
  )

  ; total-cost viene aumentato a ogni azione: il planner puo' minimizzarlo.
  (:functions
    (total-cost)
  )

  ; L'esploratore si muove lungo collegamenti liberi o da liberare.
  (:action move-explorer
    :parameters (?r - robot ?from - location ?to - location)

    :precondition (and
      (explorer ?r)
      (at ?r ?from)
      (link ?from ?to)
      (clear ?from ?to)
    )

    :effect (and
      (not (at ?r ?from))
      (at ?r ?to)
      (increase (total-cost) 1)
    )
  )

  ; Il medico si muove solo su varchi liberi e verso celle gia' sicure.
  (:action move-medic
    :parameters (?r - robot ?from - location ?to - location)

    :precondition (and
      (medical ?r)
      (at ?r ?from)
      (link ?from ?to)
      (clear ?from ?to)
      (secured ?to)
    )

    :effect (and
      (not (at ?r ?from))
      (at ?r ?to)
      (increase (total-cost) 1)
    )
  )

  ; Il soccorritore civile puo' attraversare un varco libero anche se la cella
  ; non e' ancora sicura per il medico, perche' deve prepararla.
  (:action move-civilian
    :parameters (?r - robot ?from - location ?to - location)

    :precondition (and
      (civilian ?r)
      (at ?r ?from)
      (link ?from ?to)
      (clear ?from ?to)
    )

    :effect (and
      (not (at ?r ?from))
      (at ?r ?to)
      (increase (total-cost) 1)
    )
  )

  ; Il soccorritore civile libera un collegamento ostruito da macerie.
  ; L'effetto rende attraversabile il varco in entrambe le direzioni.
  (:action remove-rubble-civilian
    :parameters (?r - robot ?from - location ?to - location)

    :precondition (and
      (civilian ?r)
      (at ?r ?from)
      (link ?from ?to)
      (rubble ?from ?to)
    )

    :effect (and
      (not (rubble ?from ?to))
      (not (rubble ?to ?from))
      (clear ?from ?to)
      (clear ?to ?from)
      (increase (total-cost) 4)
    )
  )

  ; Anche l'esploratore puo' liberare un collegamento ostruito da macerie.
  (:action remove-rubble-explorer
    :parameters (?r - robot ?from - location ?to - location)

    :precondition (and
      (explorer ?r)
      (at ?r ?from)
      (link ?from ?to)
      (rubble ?from ?to)
    )

    :effect (and
      (not (rubble ?from ?to))
      (not (rubble ?to ?from))
      (clear ?from ?to)
      (clear ?to ?from)
      (increase (total-cost) 4)
    )
  )

  ; Solo il civile mette in sicurezza la cella in cui si trova.
  (:action secure-area
    :parameters (?r - robot ?l - location)

    :precondition (and
      (civilian ?r)
      (at ?r ?l)
    )

    :effect (and
      (secured ?l)
      (increase (total-cost) 2)
    )
  )
)
