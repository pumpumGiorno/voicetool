"""Офлайн-перевод на русский.

Движок — CTranslate2 (уже стоит вместе с faster-whisper) + sentencepiece.
Модели берём в формате Argos Translate из их открытого индекса: пара языков
скачивается один раз (нужен интернет), дальше перевод считается локально,
текст никуда не отправляется.
"""
import json
import logging
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
HEADERS = {"User-Agent": "voice-tool/1.0 (+local speech tool)"}
PIVOT = "en"  # прямых пар мало, почти всё ходит через английский
MAX_CHARS = 400  # длинные предложения режем — модель обучена на предложениях

LANG_NAMES = {
    "ru": "русский", "en": "английский", "de": "немецкий", "fr": "французский",
    "es": "испанский", "it": "итальянский", "pt": "португальский", "pl": "польский",
    "uk": "украинский", "be": "белорусский", "kk": "казахский", "tr": "турецкий",
    "zh": "китайский", "ja": "японский", "ko": "корейский", "ar": "арабский",
    "nl": "нидерландский", "cs": "чешский", "sv": "шведский", "fi": "финский",
    "he": "иврит", "hi": "хинди", "id": "индонезийский", "ro": "румынский",
    "el": "греческий", "hu": "венгерский", "da": "датский", "no": "норвежский",
    "bg": "болгарский", "sr": "сербский", "sk": "словацкий", "sl": "словенский",
    "lt": "литовский", "lv": "латышский", "et": "эстонский", "fa": "персидский",
    "th": "тайский", "vi": "вьетнамский", "az": "азербайджанский", "ca": "каталанский",
}


def lang_name(code):
    return LANG_NAMES.get(code, code or "неизвестен")


def split_sentences(text):
    """Простое деление на предложения; длинные дополнительно режем по пробелу."""
    parts = [s.strip() for s in re.split(r"(?<=[.!?…])\s+|\n+", text) if s.strip()]
    out = []
    for part in parts:
        while len(part) > MAX_CHARS:
            cut = part.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > MAX_CHARS // 2 else MAX_CHARS
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            out.append(part)
    return out


class _Pair:
    """Одна языковая пара: модель CTranslate2 + её sentencepiece-словарь."""

    def __init__(self, path: Path):
        import ctranslate2
        import sentencepiece

        self.translator = ctranslate2.Translator(str(path / "model"), device="cpu")
        self.sp = sentencepiece.SentencePieceProcessor(str(path / "sentencepiece.model"))

    def __call__(self, sentences):
        batch = [self.sp.encode(s, out_type=str) for s in sentences]
        results = self.translator.translate_batch(batch, beam_size=4, max_batch_size=8)
        return [self.sp.decode(r.hypotheses[0]) for r in results]


class OfflineTranslator:
    def __init__(self, cache_dir: Path, target="ru", offline_only=False, on_progress=None):
        """offline_only=True — не ходить в сеть даже за моделью: работаем только с тем,
        что уже скачано. on_progress(done_bytes, total_bytes) — прогресс закачки для GUI."""
        self.cache = Path(cache_dir) / "models" / "translate"
        self.target = target
        self.offline_only = offline_only
        self.on_progress = on_progress
        self._pairs = {}
        self._index = None

    def available_pairs(self):
        """Какие пары уже лежат локально — показываем в проверке системы."""
        if not self.cache.is_dir():
            return []
        return sorted(p.name for p in self.cache.glob("*_*") if p.is_dir())

    def translate(self, text, src):
        if not text or src == self.target:
            return text
        route = self._route(src)
        sentences = split_sentences(text)
        for step_from, step_to in route:
            pair = self._load(step_from, step_to)
            sentences = pair(sentences)
        return " ".join(sentences)

    def _route(self, src):
        """Прямая пара, иначе через английский. Бросает понятную ошибку, если пути нет."""
        if self._have(src, self.target):
            return [(src, self.target)]
        if src != PIVOT and self._have(src, PIVOT) and self._have(PIVOT, self.target):
            return [(src, PIVOT), (PIVOT, self.target)]
        extra = (" Включён режим «только офлайн» — модель не будет скачана автоматически."
                 if self.offline_only else "")
        raise RuntimeError(
            f"Нет офлайн-модели перевода {src} → {self.target} "
            f"(в том числе через {PIVOT}).{extra} Отключить перевод: --no-translate "
            f"или \"translator\": \"none\" в config.json."
        )

    def _have(self, src, dst):
        if (self.cache / f"{src}_{dst}").is_dir():
            return True
        return not self.offline_only and self._find_package(src, dst) is not None

    def _load(self, src, dst):
        key = (src, dst)
        if key not in self._pairs:
            path = self.cache / f"{src}_{dst}"
            if not path.is_dir():
                self._download(src, dst, path)
            self._pairs[key] = _Pair(path)
        return self._pairs[key]

    def _find_package(self, src, dst):
        if self.offline_only:
            return None
        if self._index is None:
            try:
                req = urllib.request.Request(INDEX_URL, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    self._index = json.loads(r.read())
            except Exception as e:
                log.warning("Список моделей перевода недоступен (%s). "
                            "Нужен интернет — но только один раз, чтобы скачать пару языков.", e)
                self._index = []
        for pkg in self._index:
            if pkg.get("from_code") == src and pkg.get("to_code") == dst and pkg.get("links"):
                return pkg
        return None

    def _download(self, src, dst, target_dir: Path):
        if self.offline_only:
            raise RuntimeError(f"Модель перевода {src} → {dst} не скачана, "
                               f"а режим «только офлайн» запрещает загрузку.")
        pkg = self._find_package(src, dst)
        if pkg is None:
            raise RuntimeError(f"Модель перевода {src} → {dst} недоступна.")
        url = pkg["links"][0]
        if not url.startswith("https://"):  # индекс — внешние данные, качаем только по https
            raise RuntimeError(f"Подозрительная ссылка на модель {src} → {dst}: {url}")
        log.info("Скачиваю офлайн-модель перевода %s -> %s (один раз, ~200 МБ)", src, dst)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(target_dir.parent)) as tmp:
            tmp = Path(tmp)
            archive = tmp / "package.zip"
            _download_file(url, archive, on_progress=self.on_progress)
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp / "unpacked")
            root = next((p for p in (tmp / "unpacked").iterdir() if (p / "model").is_dir()), None)
            if root is None:
                raise RuntimeError(f"Неожиданная структура архива модели {src} → {dst}")
            shutil.rmtree(root / "stanza", ignore_errors=True)  # нужна только argostranslate
            shutil.move(str(root), str(target_dir))
        log.info("Модель перевода сохранена: %s", target_dir)


def _download_file(url, dest: Path, chunk=1 << 20, on_progress=None):
    """on_progress(скачано_байт, всего_байт) — чтобы GUI мог показать полосу загрузки."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        tty = bool(getattr(sys.stderr, "isatty", lambda: False)())  # в exe stderr может быть заглушкой
        done = 0
        while True:
            block = r.read(chunk)
            if not block:
                break
            f.write(block)
            done += len(block)
            if on_progress:
                on_progress(done, total)
            if total and tty:
                print(f"\r[Перевод] {done * 100 // total:3d}%  "
                      f"{done / 1e6:.0f}/{total / 1e6:.0f} МБ", end="", file=sys.stderr, flush=True)
        if tty:
            print("", file=sys.stderr)


def get_translator(cfg, enabled=True, on_progress=None):
    """None — значит переводить не будем, вызывающий код печатает только оригинал."""
    if not enabled or cfg.translator == "none":
        return None
    if cfg.translator != "argos":
        raise SystemExit(f"Неизвестный переводчик в config.json: {cfg.translator} "
                         f"(доступно: argos, none)")
    return OfflineTranslator(cfg.data_dir, cfg.translate_to,
                             offline_only=bool(cfg.get("translate_offline_only")),
                             on_progress=on_progress)
