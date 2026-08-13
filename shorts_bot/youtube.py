"""Uploaden naar YouTube via de Data API v3.

Eenmalig autoriseren met `python -m shorts_bot.main auth`; daarna ververst de
bot het token zelf en heb je geen browser meer nodig.

Twee dingen die mensen verrassen:

  Quotum   Een upload kost 1600 van de 10.000 dagelijkse eenheden. Dat is
           maximaal zes uploads per dag, ongeacht hoeveel je rendert. De bot
           houdt het verbruik bij en weigert een upload die er niet meer in past.

  Audit    Een nieuw API-project staat bij Google als 'unaudited'. Uploads
           worden dan geforceerd op privé gezet, wat je ook meestuurt, totdat
           je project is goedgekeurd. Dat is geen fout in deze bot.
"""

from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeError(RuntimeError):
    """Uploaden naar YouTube is mislukt."""


def _imports():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise YouTubeError(
            "Google-pakketten ontbreken — pip install -r requirements-shorts.txt"
        ) from exc
    return Request, Credentials, InstalledAppFlow, build, MediaFileUpload


def get_credentials(config, allow_browser: bool = False):
    """Haal een geldig token op; vernieuw of vraag opnieuw aan waar nodig."""
    Request, Credentials, InstalledAppFlow, _, _ = _imports()

    token_path = Path(config.youtube_token_file)
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif allow_browser:
        if not Path(config.youtube_client_secrets).exists():
            raise YouTubeError(
                f"{config.youtube_client_secrets} niet gevonden. Maak in Google Cloud "
                "een OAuth-client van het type 'Desktop app' aan, download het JSON-"
                "bestand en zet het pad in YOUTUBE_CLIENT_SECRETS."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(config.youtube_client_secrets), SCOPES
        )
        creds = flow.run_local_server(port=0)
    else:
        raise YouTubeError(
            "geen geldig YouTube-token — draai eerst: python -m shorts_bot.main auth"
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    return creds


def authorise(config) -> str:
    """Eenmalige autorisatie in de browser."""
    get_credentials(config, allow_browser=True)
    return str(config.youtube_token_file)


def build_body(script, niche, config) -> dict:
    """De metadata die met de video meegaat."""
    title = script.title.strip()
    if len(title) > 100:
        title = title[:97].rstrip() + "..."
    return {
        "snippet": {
            "title": title,
            "description": script.full_description(niche),
            "tags": script.tags[:15],
            "categoryId": niche.youtube_category,
        },
        "status": {
            "privacyStatus": config.privacy,
            "selfDeclaredMadeForKids": config.made_for_kids,
        },
    }


def upload(config, video_path: Path, script, niche) -> str:
    """Upload de video en geef het YouTube-id terug."""
    _, _, _, build, MediaFileUpload = _imports()

    creds = get_credentials(config)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    media = MediaFileUpload(
        str(video_path), chunksize=4 * 1024 * 1024, resumable=True, mimetype="video/mp4"
    )
    request = youtube.videos().insert(
        part="snippet,status", body=build_body(script, niche, config), media_body=media
    )

    response = None
    while response is None:
        try:
            _, response = request.next_chunk()
        except Exception as exc:  # pragma: no cover - netwerk
            raise YouTubeError(f"upload afgebroken: {exc}") from exc

    video_id = response.get("id", "")
    if not video_id:
        raise YouTubeError(f"YouTube gaf geen video-id terug: {response}")
    return video_id
