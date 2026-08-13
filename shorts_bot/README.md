# Shorts-bot

Genereert en publiceert **YouTube Shorts**, volledig lokaal op je eigen GPU.
Geen API-kosten per video: het videomodel, de stem en de ondertiteling draaien
allemaal op je eigen machine.

Twee niches, afgewisseld: **markets & finance** en **horror & mystery**.

> Deze bot staat los van de trading-bot in [`bot/`](../bot/) en
> [`trade_bot/`](../trade_bot/) — hij deelt alleen de repository, geen code.

## Wat er per video gebeurt

| # | Stap | Waarmee | Draait op |
|---|------|---------|-----------|
| 1 | Idee, script, titel en beschrijving | Claude | API |
| 2 | Voice-over inspreken | Kokoro-82M | **CPU** |
| 3 | Zes clips van 5s, met sfeergeluid | LTX-2.3 Q4 | **GPU** |
| 4 | Woord-timings uit de stem halen | faster-whisper | CPU |
| 5 | Ondertitels inbranden, geluid mixen, opschalen | ffmpeg | CPU |
| 6 | Uploaden als privé | YouTube Data API v3 | API |

De stem draait bewust op de **CPU**. Op een 10 GB-kaart is het VRAM de
bottleneck, en dat is volledig voor LTX.

Stap 4 timet de **kale** stem, vóórdat het sfeergeluid van LTX eronder wordt
gemixt. Op schone narratie ligt de afwijking rond de 30-40 ms; met
achtergrondgeluid loopt dat op naar 80-100 ms. Die volgorde is gratis
nauwkeurigheid.

## Wat het kost

Niets per video. Alleen stroom en de eenmalige schijfruimte voor de modellen.
De enige doorlopende kosten zijn de Claude-aanroepen voor de scripts: enkele
centen per video.

Op een **RTX 3080 (10 GB)** duurt een clip van 5 seconden met audio ongeveer
2 tot 3 minuten. Een Short van 30 seconden is dus 15 à 20 minuten rekentijd,
en drie per dag ongeveer een uur. Prima om 's nachts te laten lopen.

## Installatie

### 1. Python-pakketten

```bash
pip install -r requirements-shorts.txt
```

### 2. ffmpeg

```bash
sudo apt install ffmpeg        # Debian/Ubuntu
brew install ffmpeg            # macOS
winget install Gyan.FFmpeg     # Windows
```

Controleer daarna dat `ffmpeg -version` werkt in een nieuwe terminal; op
Windows moet de map met `ffmpeg.exe` in je PATH staan.

### 2b. Windows

Op Windows zijn er drie dingen anders. Sla dit niet over — het zijn precies
de plekken waar de installatie stukloopt.

**Python 3.12, niet 3.13.** Een deel van de afhankelijkheden van Kokoro
(`misaki`, en `numpy<2.0`) ondersteunt 3.13 nog niet:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-shorts.txt
```

**espeak-ng erbij voor de stem.** Kokoro zet tekst om in klanken via espeak-ng,
en dat zit op Windows niet in het pip-pakket. Zonder deze stap krijg je
`RuntimeError: espeak not installed on your system`. Installeer het van
[github.com/espeak-ng/espeak-ng/releases](https://github.com/espeak-ng/espeak-ng/releases)
en zorg dat `espeak-ng.exe`, `libespeak-ng.dll` en de map `espeak-ng-data` in
je PATH staan.

> Lukt dat niet, dan hoef je niet vast te lopen: zet `SHORTS_TTS_BACKEND=command`
> en geef in `SHORTS_TTS_COMMAND` een andere TTS-engine op. De bot gebruikt dan
> jouw commando en heeft Kokoro helemaal niet nodig.

**LTX zonder `--extra natten`.** Die extra bouwt een CUDA-uitbreiding die
alleen voor Linux is bedoeld en op Windows vrijwel zeker faalt:

```powershell
uv sync
```

**Geen systemd.** `deploy/shorts-bot.service` is Linux-only. Op Windows laat je
`python -m shorts_bot.main run` gewoon in een terminalvenster staan, of je maakt
een taak in Taakplanner die dat commando bij het inloggen start.

Gebruik in `.env` schuine strepen in paden (`C:/Users/jij/LTX-2`); dat werkt op
Windows net zo goed en scheelt gedoe met backslashes.

### 3. Het videomodel — kies je route

Er zijn twee manieren om LTX te draaien, en welke past hangt af van je kaart.

| | VRAM | Download | Audio | Snelheid |
|---|---|---|---|---|
| **ComfyUI + GGUF** (`comfy`) | vanaf ~8 GB | 14-17 GB | ja | 2-3 min per clip van 5s |
| **Officiële opdrachtregel** (`ltx`) | 24 GB+ | 46 GB | ja | sneller, maar past niet op kleine kaarten |

**Met 10 of 12 GB VRAM neem je ComfyUI.** De officiële opdrachtregel wil de
volledige gewichten van 46 GB; die passen niet, ook niet met uitwijken naar je
werkgeheugen. ComfyUI werkt met gekwantiseerde GGUF-versies en parkeert wat niet
past in je RAM — trager, maar het draait. Reken op 32 GB werkgeheugen.

#### 3a. Route ComfyUI (aanbevolen bij weinig VRAM)

1. Installeer [ComfyUI](https://github.com/comfyanonymous/ComfyUI).
2. Installeer de LTXVideo-nodes: in ComfyUI Manager zoeken op *LTXVideo*, of
   `git clone https://github.com/Lightricks/ComfyUI-LTXVideo` in `custom_nodes/`.
3. Haal de vijf onderdelen op en zet ze in de juiste map van ComfyUI:

   | Onderdeel | Map |
   |---|---|
   | de gekwantiseerde transformer (`...-Q4_K_S.gguf`) | `models/unet/` |
   | de Gemma-tekstencoder (GGUF) | `models/clip/` |
   | de tekstprojectie (`...text_projection...safetensors`) | `models/clip/` |
   | de video-VAE | `models/vae/` |
   | de audio-VAE | `models/vae/` |

   De actuele bestandsnamen staan in de
   [officiële ComfyUI-handleiding voor LTX-2.3](https://docs.comfy.org/tutorials/video/ltx/ltx-2-3).
   De GGUF-bestanden komen van community-repositories zoals `QuantStack/LTX-2.3-GGUF`
   of `unsloth/LTX-2.3-GGUF`.

   > **Belangrijkste valkuil:** pak niet zomaar een willekeurige Gemma-GGUF. De
   > loader verwacht precies de variant die bij deze workflow hoort; een andere
   > heeft andere tensornamen en laadt niet.

4. Bouw in ComfyUI een werkende tekst-naar-video workflow en render één clip met
   de hand. Werkt dat niet, dan werkt de bot ook niet — los het daar eerst op.
5. Exporteer via **Workflow → Export (API)**. Let op: dat is een ánder bestand
   dan de gewone export.
6. Zet in dat bestand de plaatshouders op de plekken die per video wisselen:

   | Plaatshouder | Waar |
   |---|---|
   | `%PROMPT%` | het tekstveld van je positieve prompt |
   | `%SEED%` | de seed van de sampler |
   | `%WIDTH%` / `%HEIGHT%` | de afmetingen van de lege latent |
   | `%FRAMES%` | het aantal frames |

   `%PROMPT%` mag middenin een langere tekst staan, bijvoorbeeld
   `"%PROMPT%, cinematic, film grain"`. De rest vervang je één-op-één.

7. Zet `SHORTS_VIDEO_BACKEND=comfy` en `COMFY_WORKFLOW` in `.env`, laat ComfyUI
   draaien, en draai `python -m shorts_bot.main doctor`.

Er staat bewust geen enkele node-naam in de code: de bot vult alleen de
plaatshouders in en stuurt jouw workflow door. Verbouw je hem, of hernoemt
ComfyUI zijn nodes, dan blijft de bot werken.

#### 3b. Route officiële opdrachtregel (24 GB VRAM of meer)

Het videomodel zit in een eigen repository met eigen gewichten:

```bash
git clone https://github.com/Lightricks/LTX-2
cd LTX-2
uv sync --extra natten     # op Windows: alleen `uv sync`, zie 2b
hf download Lightricks/LTX-2.3
```

Zet daarna `LTX_REPO` in `.env` naar die map, en de vier modelpaden naar de
bestanden die je hebt gedownload. Met 10 GB VRAM wil je de **Q4_K_S**-variant;
grotere varianten passen niet.

> **Model-ID's en vlagnamen verschillen per LTX-versie.** `python -m
> shorts_bot.main doctor` draait `--help` op jouw installatie en toont de
> beschikbare vlaggen, zodat je `LTX_WIDTH_FLAG` en `LTX_HEIGHT_FLAG` kunt
> controleren zonder in de code te duiken.

### 4. Sleutels

Kopieer `.env.example` naar `.env` en vul in:

- `ANTHROPIC_API_KEY` — voor de scripts
- `YOUTUBE_CLIENT_SECRETS` — pad naar je OAuth-bestand (zie hieronder)

### 5. YouTube

1. Maak in [Google Cloud Console](https://console.cloud.google.com/) een project.
2. Zet **YouTube Data API v3** aan.
3. Maak een OAuth-client van het type **Desktop app** en download het JSON-bestand.
4. Autoriseer eenmalig:

```bash
python -m shorts_bot.main auth
```

Daarna ververst de bot het token zelf en heb je geen browser meer nodig.

## Gebruik

```bash
python -m shorts_bot.main doctor     # controleer wat er klaarstaat
python -m shorts_bot.main script     # alleen een script, geen GPU, geen upload
python -m shorts_bot.main once       # één complete video, nu
python -m shorts_bot.main run        # de planner, 2-3 per dag
python -m shorts_bot.main status     # wat is er geplaatst, hoeveel quotum over
```

Begin met `script`: die kost een paar cent en laat zien wat voor video's je
krijgt, zonder een minuut GPU-tijd. Draai daarna `once` met `SHORTS_DRY_RUN=1`
om een complete video te maken zonder hem te publiceren.

24/7 draaien gaat met de systemd-service in
[`deploy/shorts-bot.service`](../deploy/shorts-bot.service).

## Zelf monteren in CapCut

Wil je een video met de hand oppoetsen in plaats van hem automatisch te laten
plaatsen, gebruik dan de exportstand:

```bash
python -m shorts_bot.main once --export
```

De bot rendert dan alles, uploadt niets, en zet de onderdelen los in
`shorts_export/`:

| Bestand | Wat het is |
|---------|------------|
| `short.mp4` | Wat de bot zelf zou hebben gepost — als referentie of om zo te uploaden |
| `shots/` | De losse clips, op volgorde, met sfeergeluid maar zonder stem |
| `voice.wav` | De kale voice-over |
| `captions.srt` | De ondertitels — **dit is het bestand voor CapCut** |
| `captions.ass` | Dezelfde ondertitels met de opmaak van de bot; niet voor CapCut |
| `script.txt` | De narratie per shot, plus de gebruikte beeldprompts |
| `youtube.txt` | Titel, beschrijving en tags om te plakken |
| `LEESMIJ.txt` | De importstappen |

In CapCut: **Tekst → Automatische ondertiteling → Lokale ondertiteling →
Importeren**, en kies `captions.srt`. Houd je het sfeergeluid van de clips
aan, zet het dan zo'n 18 dB onder de stem, anders praat het eroverheen.

> Ondertitels importeren kan alleen in CapCut op de computer of in de browser.
> De mobiele app kan geen ondertitelbestand inlezen.

Wil je dit altijd, zet dan `SHORTS_EXPORT=1` in `.env`.

## Twee dingen die mensen verrassen

**Je nieuwe API-project staat als "unaudited".** Google zet dan élke upload op
privé, wat `SHORTS_PRIVACY` ook zegt, totdat je project handmatig is
goedgekeurd. Dat is geen fout in deze bot. Vraag de audit aan via de Cloud
Console als je publiek wilt posten.

**Een upload kost 1600 van je 10.000 dagelijkse quotum-eenheden.** Dat is
maximaal zes uploads per dag, ongeacht hoeveel je rendert. De bot houdt het
verbruik bij en weigert een upload die er niet meer in past. Bij 2-3 per dag
zit je ruim.

## Een derde niche toevoegen

Nichepakketten zijn data, geen code. Schrijf een blok bij in
[`niches.py`](niches.py) — de opdracht aan de schrijver, de beeldstijl, de
ondertitelstijl en de YouTube-categorie — en zet de sleutel erbij in
`SHORTS_NICHES`. Verder verandert er niets aan de bot.

Elke niche draagt ook een **nauwkeurigheidsregel** mee. Bij finance is dat:
gebruik alleen cijfers waar je zeker van bent. Bij horror: als het verhaal op
een betwiste bron rust, moet het script dat in de laatste twee regels hardop
zeggen. Dat is geen nettigheid maar strategie — YouTube's beleid rond
misleidende content raakt faceless-kanalen hard, en "the story is real, the
ship may not be" is bovendien een sterkere afsluiter dan doen alsof.

## Ondertitelstijlen

| Stijl | Wat je ziet | Gebruikt door |
|-------|-------------|---------------|
| `block` | Hele zin op een donker blok, actief woord gemarkeerd | markets & finance |
| `pop` | Vier woorden, actief woord springt op en kleurt | horror & mystery |
| `word` | Eén woord tegelijk, groot en centraal | — |

`block` voor finance omdat percentages, jaartallen en namen moeten blijven
staan; `pop` voor horror omdat daar het ritme zwaarder weegt dan de precieze
woorden. Per niche in te stellen.

## Zonder GPU testen

De bot heeft een dummy-videobackend die met ffmpeg een testclip maakt. Daarmee
draait de hele keten op een machine zonder GPU:

```bash
SHORTS_VIDEO_BACKEND=dummy SHORTS_TTS_BACKEND=silent SHORTS_DRY_RUN=1 \
  python -m shorts_bot.main once
```

## Tests

```bash
python -m unittest discover -s tests -p "test_shorts_*.py"
```
