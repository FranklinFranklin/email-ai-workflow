# AI E-mail Workflow

---

## Wat doet dit? (uitleg voor iedereen)

Stel je voor: je krijgt elke dag heel veel e-mails op je werk.
Sommige zijn vragen, sommige zijn klachten, sommige gaan over facturen.
Het kost veel tijd om al die e-mails te lezen én te beantwoorden.

**Dit programma helpt daarmee.** Het leest je e-mails, begrijpt waar
ze over gaan, en schrijft alvast een antwoord voor je klaar.

Jij beslist dan of je dat antwoord verstuurt, aanpast, of weggooiet.

> 📬 **Het programma verstuurt nooit zelf iets.**
> Jij drukt altijd zelf op "verzenden".

Dat is expres zo gebouwd — want een computer kan fouten maken,
en jij weet het beste wat je klanten nodig hebben.

---

## Hoe werkt het in het kort?

```
1. E-mail komt binnen
        │
        ▼
2. Het programma leest de e-mail
   (privégegevens worden meteen afgeschermd)
        │
        ▼
3. AI bepaalt: is dit een klacht? een vraag? een factuur?
        │
        ▼
4. AI schrijft een conceptantwoord
        │
        ▼
5. Jij ziet het concept, past het aan als nodig,
   en verstuurt het zelf via je eigen e-mailprogramma
```

---

## Wat heb je nodig om te beginnen?

| Wat | Waar te halen |
|---|---|
| **Docker Desktop** | [docker.com/get-started](https://www.docker.com/get-started) |
| **Een Anthropic API-sleutel** | [console.anthropic.com](https://console.anthropic.com) |
| **Een Google Cloud-project** met Gmail API ingeschakeld | [console.cloud.google.com](https://console.cloud.google.com) |
| **Liever geen cloudservice?** | zie regel 88 voor de lokale versie

---

## Installatie — stap voor stap

### Stap 1 — Download de code

```bash
git clone <repo-url>
cd email-ai-workflow
```

### Stap 2 — Maak je instellingenbestand aan

```bash
cp .env.example .env
```

Open `.env` in een teksteditor en vul de verplichte velden in:

```
# Minimaal verplicht om te starten:

APP_SECRET_KEY=         ← vul hier een willekeurige lange tekst in (min. 32 tekens)
POSTGRES_PASSWORD=      ← kies een sterk wachtwoord voor de database
REDIS_PASSWORD=         ← kies een sterk wachtwoord voor de cache
ANTHROPIC_API_KEY=      ← plak hier je Anthropic API-sleutel (begint met sk-ant-...)
GMAIL_CLIENT_ID=        ← van je Google Cloud-project
GMAIL_CLIENT_SECRET=    ← van je Google Cloud-project
```

> **Liever geen cloudservice?** Zie hieronder hoe je Ollama gebruikt met een lokaal model.

---

## Optie: Ollama gebruiken (lokaal model, geen API-sleutel nodig)

Met Ollama draait het AI-model volledig op jouw eigen computer.
Geen API-kosten, geen internet vereist voor de AI, en je gegevens verlaten je machine niet.

> Je hebt wel een redelijk krachtige computer nodig (minimaal 8 GB RAM aanbevolen).

### Stap 1 — Installeer Ollama

Ga naar [ollama.com](https://ollama.com) en download de installer voor jouw systeem.
Start Ollama na installatie — het draait als achtergrondprogramma op poort `11434`.

### Stap 2 — Download een model

Open een terminal en typ:

```bash
ollama pull llama3.2
```

Dit downloadt het LLaMA 3.2-model (~2 GB). Andere goede opties:

| Model | Commando | Grootte | Kwaliteit |
|---|---|---|---|
| LLaMA 3.2 (aanbevolen) | `ollama pull llama3.2` | ~2 GB | ⭐⭐⭐⭐ |
| Mistral | `ollama pull mistral` | ~4 GB | ⭐⭐⭐⭐ |
| Qwen 2.5 | `ollama pull qwen2.5` | ~4 GB | ⭐⭐⭐⭐ |
| Phi-3 Mini (snel, licht) | `ollama pull phi3` | ~2 GB | ⭐⭐⭐ |

### Stap 3 — Stel Ollama in als provider

Pas je `.env`-bestand aan:

```
# Schakel over naar Ollama
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.2       ← gebruik de naam van het model dat je hebt gedownload

# ANTHROPIC_API_KEY is niet meer verplicht bij LLM_PROVIDER=ollama
ANTHROPIC_API_KEY=
```

### Stap 4 — Controleer of Ollama draait

```bash
curl http://localhost:11434/api/tags
# Verwacht: een lijst met gedownloade modellen
```

### Stap 5 — Start de applicatie zoals normaal

```bash
docker compose up -d
```

Dat is alles. De app gebruikt nu automatisch het lokale model.

> **Wisselen tussen Anthropic en Ollama?**
> Pas alleen `LLM_PROVIDER=` aan in `.env` en herstart de applicatie.
> Alle andere instellingen blijven hetzelfde.

---

Genereer een versleutelingssleutel voor privégegevens:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Plak de uitvoer als waarde van `ENCRYPTION_KEY=` in je `.env`.

### Stap 3 — Start het programma

```bash
docker compose up -d
```

Dit start automatisch:
- De applicatie (poort `8000`)
- De database (PostgreSQL)
- De cache (Redis)

Controleer of alles draait:

```bash
curl http://localhost:8000/healthz
# Verwacht: {"status": "ok"}
```

### Stap 4 — Stel de database in

```bash
docker compose exec api alembic upgrade head
```

### Stap 5 — Verbind Gmail

Ga naar `http://localhost:8000/docs` en gebruik de OAuth-endpoints
om je Gmail-account te koppelen. Het systeem vraagt alleen
**leestoegang** — het kan nooit zelf e-mails versturen.

---

## Dagelijks gebruik

Nadat alles draait:

1. **Nieuwe e-mails komen automatisch binnen** via Gmail.
2. **Open het reviewdashboard** (standaard op `http://localhost:8000/docs`).
3. **Je ziet per e-mail:**
   - De originele e-mail
   - De categorie (bijv. *Klacht*, *Factuur*, *Vraag*)
   - Een kant-en-klaar conceptantwoord
4. **Keur goed, bewerk, of verwerp** het concept.
5. **Goedgekeurd?** Je e-mailprogramma opent zich met het concept ingevuld.
   Jij verstuurt het.

---

## Veelgestelde vragen

**Kan het programma per ongeluk e-mails versturen?**
Nee. Het systeem heeft technisch geen toegang om te verzenden.
De Gmail-koppeling heeft uitsluitend leesrechten.

**Wat gebeurt er met mijn e-mailgegevens?**
Privégegevens (namen, e-mailadressen, IBAN's) worden versleuteld
opgeslagen en nooit naar externe AI-diensten gestuurd zonder
anonimisering.

**Wat als de AI een fout maakt in het concept?**
Je ziet altijd het concept vóórdat het verstuurd wordt.
Bij twijfel van de AI staat er een waarschuwing bij het concept.

**Hoe stop ik het programma?**
```bash
docker compose down
```

---

## Technisch overzicht (voor ontwikkelaars)

```
email-ai-workflow/
├── agents/          LLM-agents (classificatie + conceptgeneratie)
├── api/             FastAPI-routes en middleware
├── config/          Instellingen en logging
├── database/        Databaseverbinding en repositories
├── models/          ORM-modellen en Pydantic-schema's
├── security/        Versleuteling, PII-detectie, auditlog
├── services/        Gmail-integratie en verwerkingslogica
└── tests/           Unit-, integratie- en securitytests
```

Zie de technische documentatie in `docs/` voor architectuurdiagrammen
en API-specificaties.

---

## Licentie

Intern gebruik — zie `LICENSE` voor details.
