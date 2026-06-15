import json
import os
from urllib import error as url_error
from urllib import request


class LLMResult:
    def __init__(self, provider, model, text, used_fallback=False, error=None):
        #Campi minimi che main.py usa per stampare diagnostica e contenuto
        self.provider = provider
        self.model = model
        self.text = text
        self.used_fallback = used_fallback
        self.error = error


def extract_json_text(text):
    #Molti modelli locali aggiungono ```json o una frase introduttiva.
    #Qui teniamo solo il blocco tra la prima "{" e l'ultima "}"
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    json.loads(cleaned)
    return cleaned


def is_quota_error(error_text):
    #Groq puo' restituire messaggi testuali diversi: controlliamo marker comuni
    lowered = error_text.lower()
    quota_markers = [
        "429",
        "quota",
        "rate limit",
        "resource_exhausted",
        "too many requests",
        "insufficient_quota",
    ]
    return any(marker in lowered for marker in quota_markers)


class LocalClient:
    def __init__(self, model=None):
        #Nome fittizio, utile solo nella stampa diagnostica
        self.model = model or "deterministic"

    def generate(self, prompt, fallback_text):
        #Il prompt viene ignorato: il fallback e' gia' costruito dal programma
        return LLMResult("local", self.model, fallback_text, used_fallback=True)

    def generate_json(self, prompt, fallback_data):
        #Serializzo il fallback per mantenere la stessa interfaccia degli LLM veri
        fallback_text = json.dumps(fallback_data, ensure_ascii=False, indent=2)
        return LLMResult("local", self.model, fallback_text, used_fallback=True)


class OllamaClient:
    def __init__(self, model=None, role=None):
        #Se il client nasce per explorer/medic, legge il modello specifico del ruolo
        role_model_key = f"{role.upper()}_OLLAMA_MODEL" if role else None
        self.model = (
            model
            or (os.getenv(role_model_key) if role_model_key else None)
            or os.getenv("OLLAMA_MODEL")
            or "llama3.2:3b"
        )
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.timeout = float(os.getenv("OLLAMA_TIMEOUT", "120"))

    #Offline forza il percorso deterministico senza contattare Ollama
    def generate(self, prompt, fallback_text):
        if os.getenv("RESCUE_OFFLINE") == "1":
            return LLMResult(
                "ollama",
                self.model,
                fallback_text,
                used_fallback=True,
                error="RESCUE_OFFLINE=1",
            )

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                #Temperature a 0: vogliamo confronti ripetibili tra modelli.
                "temperature": 0,
            },
        }

        try:
            text = self._post_generate(payload).strip()
            if not text:
                return LLMResult(
                    "ollama",
                    self.model,
                    fallback_text,
                    used_fallback=True,
                    error="Risposta vuota",
                )
            return LLMResult("ollama", self.model, text)
        except Exception as exc:
            return LLMResult("ollama", self.model, fallback_text, used_fallback=True, error=str(exc))
        
    #Il chiamante si aspetta JSON valido; se il modello sbaglia, torna fallback
    def generate_json(self, prompt, fallback_data):
        fallback_text = json.dumps(fallback_data, ensure_ascii=False, indent=2)
        prompt = (
            "Rispondi solo con JSON valido, senza markdown.\n\n"
            + prompt
        )
        result = self.generate(prompt, fallback_text)

        try:
            result.text = extract_json_text(result.text)
            return result
        except Exception as exc:
            return LLMResult("ollama", self.model, fallback_text, used_fallback=True, error=f"Risposta non JSON valida: {exc}")
        
    #Uso urllib per non aggiungere dipendenze: Ollama espone una semplice API HTTP
    def _post_generate(self, payload):
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except url_error.URLError as exc:
            raise RuntimeError(
                f"Ollama non raggiungibile su {self.base_url}. Avvia Ollama e verifica il modello {self.model}."
            ) from exc

        return data.get("response", "")


class GroqClient:
    def __init__(self, model=None):
        self.model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.api_key = os.getenv("GROQ_API_KEY")
        self.disabled_error = None

    def generate(self, prompt, fallback_text):
        #Se manca la chiave o siamo offline, non interrompiamo il flusso
        if os.getenv("RESCUE_OFFLINE") == "1" or not self.api_key:
            reason = "RESCUE_OFFLINE=1" if os.getenv("RESCUE_OFFLINE") == "1" else "GROQ_API_KEY mancante"
            return LLMResult("groq", self.model, fallback_text, used_fallback=True, error=reason)
        if self.disabled_error:
            return LLMResult("groq", self.model, fallback_text, used_fallback=True, error=self.disabled_error)

        try:
            from groq import Groq

            client = Groq(api_key=self.api_key)
            # Temperature a 0: vogliamo risposte stabili, non creative, perche'
            # i dati finiscono poi nel triage e nel planner.
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            text = response.choices[0].message.content.strip()
            if not text:
                return LLMResult(
                    "groq",
                    self.model,
                    fallback_text,
                    used_fallback=True,
                    error="Risposta vuota",
                )
            return LLMResult("groq", self.model, text)
        except Exception as exc:
            error = str(exc)
            if is_quota_error(error):
                self.disabled_error = error
            return LLMResult("groq", self.model, fallback_text, used_fallback=True, error=error)

    def generate_json(self, prompt, fallback_data):
        #Groq supporta system prompt. lo usiamo per irrigidire l'output JSON
        fallback_text = json.dumps(fallback_data, ensure_ascii=False, indent=2)
        if os.getenv("RESCUE_OFFLINE") == "1" or not self.api_key:
            reason = "RESCUE_OFFLINE=1" if os.getenv("RESCUE_OFFLINE") == "1" else "GROQ_API_KEY mancante"
            return LLMResult("groq", self.model, fallback_text, used_fallback=True, error=reason)
        if self.disabled_error:
            return LLMResult("groq", self.model, fallback_text, used_fallback=True, error=self.disabled_error)

        try:
            from groq import Groq

            client = Groq(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        #Qui siamo severi perche' dopo il triage viene letto
                        #automaticamente come dizionario Python.
                        "content": "Rispondi solo con JSON valido, senza markdown.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
            text = response.choices[0].message.content.strip()
            text = extract_json_text(text)
            return LLMResult("groq", self.model, text)
        except Exception as exc:
            error = str(exc)
            if is_quota_error(error):
                self.disabled_error = error
            return LLMResult("groq", self.model, fallback_text, used_fallback=True, error=error)

#Crea il client richiesto dal nome scritto nel .env.
def create_llm_client(provider, role=None):
    #Il solo provider remoto ammesso e' Groq
    clients = {
        "groq": GroqClient,
        "local": LocalClient,
        "ollama": OllamaClient,
    }
    normalized = provider.lower().strip()

    if normalized not in clients:
        available = ", ".join(sorted(clients))
        raise ValueError(f"Provider LLM non valido: {provider}. Valori: {available}.")

    if normalized == "ollama":
        return clients[normalized](role=role)

    return clients[normalized]()
