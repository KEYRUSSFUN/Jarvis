# Управление программами и файлами — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jarvis открывает файлы по названию, печатает текст и нажимает клавиши в нужном окне, закрывает программы из трея и запускает игры по ярлыкам с рабочего стола.

**Architecture:** Три новых модуля инструментов (`files.py`, `keys.py`, `processes.py`) по образцу `apps.py`: логика выбора — в функциях инструментов и чистых функциях (юнит-тесты с подменой backend'а), вызовы WinAPI — в классах `Windows*` и общем модуле `winapi.py`. `build_registry` связывает всё вместе; общий `LaunchLog` сообщает инструментам печати, что недавно что-то запускалось.

**Tech Stack:** Python 3.11, ctypes (user32/shell32/version), psutil, pynput 1.8.2, Ollama qwen3:8b.

**Spec:** `docs/superpowers/specs/2026-10-28-system-control-design.md`

## Global Constraints

- Без новых зависимостей: только ctypes, psutil, pynput, rapidfuzz — уже в `pyproject.toml`.
- Сравнение названий — только через `jarvis.tools.matching.find_best` (порог `MIN_SCORE = 70`).
- Папки пользователя — через `SHGetKnownFolderPath`, не `Path.home() / "Desktop"` (рабочий стол в OneDrive).
- Никогда не печатать, если нужное окно не стало активным; никогда не `kill` процесс с видимым окном;
  никогда не трогать процессы из `C:\Windows`, `explorer`, Ollama, сам Jarvis и его родителей.
- Любая правка промпта или описаний инструментов — с прогоном `uv run pytest -m llm`;
  планка — не хуже текущей (провал допустим только «Включи звук обратно»).
- Комментарии и тексты для пользователя — по-русски, в стиле существующего кода.
- Коммиты — только если пользователь попросит (шаги «commit» в задачах не выполнять без его слова).

## Review Focus

1. Модель передаёт в `window` название файла с расширением или без («Отчёт», «Отчёт.docx»), а заголовок окна — «Отчёт.docx - Word»: окно должно находиться. → тест в Task 3.
2. Пользователь говорит «Закрой стим» сразу после запуска Steam, когда у него ещё есть видимое окно: закрывается окно (WM_CLOSE), процесс не убивается. → тест в Task 4.
3. Название совпадает и с файлом, и с приложением («Открой Discord»: в «Пуске» и ярлык на столе): `open_app` берёт «Пуск», ярлык — только если в «Пуске» нет. → тест в Task 2.
4. Текст с переводами строк и буквами «ё», кавычками-«ёлочками»: печатается как есть, `\n` → Enter. → тест в Task 3.
5. Процесс завершился сам, пока мы ждали после WM_CLOSE, или исчез между перечислением и `kill` (`psutil.NoSuchProcess`): это успех, а не ошибка. → тест в Task 4.

---

### Task 1: Общий WinAPI-модуль и игнор собственных нажатий

**Files:**
- Create: `src/jarvis/tools/winapi.py`
- Modify: `src/jarvis/tools/apps.py` (перенести `Window` и перечисление окон в `winapi.py`, `apps.py` реэкспортирует `Window`)
- Modify: `src/jarvis/audio/recorder.py:98-107`
- Test: `tests/test_audio_logic.py`

**Interfaces:**
- Produces (`winapi.py`):
  - `@dataclass(frozen=True) class Window: hwnd: int; title: str; pid: int`
  - `visible_windows() -> list[Window]` — видимые окна с непустым заголовком, без «Program Manager»
  - `process_windows(pid: int) -> list[int]` — все top-level hwnd процесса, включая скрытые
  - `post_close(hwnd: int) -> None` — `PostMessageW(hwnd, WM_CLOSE)`
  - `activate(hwnd: int) -> bool` — восстановить из свёрнутого, Alt-приём + `SetForegroundWindow`, вернуть `GetForegroundWindow() == hwnd` (проверка с повтором до ~0.5 с)
  - `foreground_window() -> int`
  - `send_text(text: str) -> None` — `SendInput` c `KEYEVENTF_UNICODE` (суррогатные пары для символов вне BMP), `\n` → VK_RETURN, `\r` пропускать
  - `send_keys(vks: list[int]) -> None` — нажать все по порядку, отпустить в обратном
- `apps.WindowsApps.windows()` и `close()` используют `winapi.visible_windows()` / `winapi.post_close()`.

- [ ] **Step 1: Тест — горячая клавиша не реагирует на нажатия, сделанные программой**

```python
def test_injected_hotkey_presses_are_ignored(monkeypatch):
    monkeypatch.setattr(recorder.sd, "InputStream", lambda **kwargs: None)
    presses = []
    ptt = recorder.PushToTalk("ctrl_l", 0.0, on_press=lambda: presses.append(1))
    ptt._on_press(keyboard.Key.ctrl_l, True)
    assert presses == []
    ptt._on_press(keyboard.Key.ctrl_l, False)
    assert presses == [1]
```

- [ ] **Step 2:** `uv run pytest tests/test_audio_logic.py -q` → FAIL (TypeError: лишний аргумент).
- [ ] **Step 3:** `_on_press(self, key, injected: bool = False)` и `_on_release(self, key, injected: bool = False)`: при `injected` — ничего не делать. Создать `winapi.py`, перенести туда перечисление окон из `WindowsApps`.
- [ ] **Step 4:** `uv run pytest -q` → все проходят (`tests/test_tools.py` по-прежнему импортирует `Window` из `apps`; поле `pid` у `Window` в `FakeApps` — передавать `pid=0`).
- [ ] **Step 5: Commit** (только по просьбе пользователя).

### Task 2: Поиск и открытие файлов, ярлыки для `open_app`

**Files:**
- Create: `src/jarvis/tools/files.py`
- Modify: `src/jarvis/tools/apps.py` (fallback на ярлыки, отметка запуска)
- Test: `tests/test_files.py`, `tests/test_tools.py`

**Interfaces:**
- Produces (`files.py`):
  - `@dataclass(frozen=True) class FileEntry: name: str; path: Path` — `name`: имя без расширения для файлов и ярлыков, полное имя для папок
  - `class LaunchLog` с `__init__(self, clock: Callable[[], float] = time.monotonic)`, `mark() -> None`, `recent(seconds: float) -> bool`
  - `scan(folders: list[Path], depth: int = 1, limit: int = 3000) -> list[FileEntry]` — пропускать скрытые/системные (`st_file_attributes & 0x6`), `desktop.ini`, имена с точкой в начале
  - `class FilesBackend(Protocol): entries() -> list[FileEntry]; open(path: Path) -> None`
  - `class WindowsFiles` — папки: FOLDERID_Desktop `{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}`, FOLDERID_PublicDesktop `{C4AA340D-F20F-4863-AFEF-F87EF2E6BA25}`, FOLDERID_Documents `{FDD39AD0-238F-46AF-ADB4-6C85480369C7}`, FOLDERID_Downloads `{374DE290-123F-4565-9164-39C4925E467B}`; `entries()` сканирует заново при каждом вызове; `open` — `os.startfile`
  - `SHORTCUT_SUFFIXES = {".lnk", ".url"}`
  - `register(registry, backend: FilesBackend, launched: LaunchLog) -> None` — инструмент `open_file(name)`; успех: `speech="Открываю {entry.name}."`, `launched.mark()`; ненайденное — `ok=False` без `speech`
- Modify `apps.register(registry, backend, aliases, *, files: FilesBackend | None = None, launched: LaunchLog | None = None, close_background: Callable[[str], ToolResult | None] | None = None)` — `close_background` используется в Task 4.

- [ ] **Step 1: Тесты**

```python
def test_scan_finds_files_folders_and_shortcuts_one_level_deep(tmp_path):
    (tmp_path / "Отчёт.docx").write_text("")
    (tmp_path / "Warframe.url").write_text("")
    (tmp_path / "Исходники" / "глубже").mkdir(parents=True)
    (tmp_path / "Исходники" / "main.py").write_text("")
    (tmp_path / "Исходники" / "глубже" / "скрыто.txt").write_text("")
    names = {e.name for e in scan([tmp_path])}
    assert {"Отчёт", "Warframe", "Исходники", "main", "глубже"} <= names
    assert "скрыто" not in names

def test_scan_skips_hidden_files(tmp_path): ...  # SetFileAttributesW(path, 2) → нет в результате

def test_open_file_opens_best_match_and_marks_launch():  # «отчет» → путь Отчёт.docx, speech «Открываю Отчёт.», launched.recent(20)
def test_open_file_unknown_name_is_error_without_speech():
def test_launch_log_recent():  # mark(); +19 c → True; +21 c → False (FakeClock)
```
В `tests/test_tools.py`:
```python
def test_open_app_falls_back_to_desktop_shortcut():  # «Warframe» нет в FakeApps → files.open(Path("Warframe.url")), speech «Открываю Warframe.»
def test_open_app_prefers_start_menu_over_shortcut():  # «телеграм» есть и в «Пуске», и ярлыком → backend.launched, files не открывались
```

- [ ] **Step 2:** `uv run pytest tests/test_files.py tests/test_tools.py -q` → FAIL (нет модуля).
- [ ] **Step 3:** Реализовать `files.py`; в `open_app` порядок: «Пуск» → ярлык (`find_best` по `entries` с суффиксом из `SHORTCUT_SUFFIXES`) → браузер по умолчанию → ошибка. Успешный запуск — `launched.mark()`.
- [ ] **Step 4:** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** (только по просьбе пользователя).

### Task 3: Печать текста и сочетания клавиш

**Files:**
- Create: `src/jarvis/tools/keys.py`
- Test: `tests/test_keys.py`

**Interfaces:**
- Consumes: `winapi.Window`, `LaunchLog` (Task 2).
- Produces (`keys.py`):
  - `parse_keys(keys: str) -> list[int]` — разделители `+` и пробел, регистр не важен; имена: `ctrl`, `shift`, `alt`, `win`, `enter`, `tab`, `esc`, `space`, `backspace`, `delete`, `home`, `end`, `pageup`, `pagedown`, `up`, `down`, `left`, `right`, `f1`–`f12`, `a`–`z`, `0`–`9`; синонимы `control`→`ctrl`, `return`→`enter`, `escape`→`esc`, `del`→`delete`; неизвестное — `ValueError` с перечнем допустимых
  - `class InputBackend(Protocol): windows() -> list[Window]; activate(hwnd: int) -> bool; type_text(text: str) -> None; press(vks: list[int]) -> None`
  - `WINDOW_WAIT_SECONDS = 15`, `RECENT_LAUNCH_SECONDS = 20`, шаг ожидания 0.1 с
  - `register(registry, backend: InputBackend, launched: LaunchLog, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None` — инструменты `type_text(text, window?)` (speech «Напечатал.») и `press_keys(keys, window?)` (speech «Готово.»)
  - `class WindowsInput` — обёртка над `winapi`

- [ ] **Step 1: Тесты**

```python
@pytest.mark.parametrize(("keys", "vks"), [
    ("ctrl+s", [0x11, 0x53]), ("Ctrl + Shift + T", [0x11, 0x10, 0x54]),
    ("enter", [0x0D]), ("alt+f4", [0x12, 0x73]), ("control+return", [0x11, 0x0D]),
])
def test_parse_keys(keys, vks): assert parse_keys(keys) == vks

def test_parse_unknown_key_lists_allowed():  # pytest.raises(ValueError, match="ctrl")
def test_type_text_activates_window_then_types():  # window «Отчёт», окна [Window(5, "Отчёт.docx - Word", 1)] → activated == [5], typed == ["привет"]
def test_type_text_window_with_extension_matches_title():  # window «Отчёт.docx» → тот же hwnd 5
def test_type_text_not_typed_if_window_did_not_activate():  # activate → False: ok=False, typed == []
def test_type_text_without_window_types_into_active():  # activated == [], typed == ["привет"]
def test_type_text_waits_for_window_after_recent_launch():  # окно появляется на 3-м опросе; launched.mark() перед вызовом → typed
def test_type_text_does_not_wait_without_recent_launch():  # окна нет, launch не было → ok=False, sleeps == []
def test_type_text_keeps_newlines_and_yo():  # «Ёлка\n«ура»» передаётся в backend.type_text без изменений
def test_press_keys_unknown_key_is_error():  # «ctrl+щ» → ok=False, pressed == []
```

- [ ] **Step 2:** `uv run pytest tests/test_keys.py -q` → FAIL.
- [ ] **Step 3:** Реализовать; поиск окна — `find_best(window, [w.title for w in windows])`, при промахе ещё раз с `Path(window).stem`.
- [ ] **Step 4:** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** (только по просьбе пользователя).

### Task 4: Закрытие программ в трее

**Files:**
- Create: `src/jarvis/tools/processes.py`
- Modify: `src/jarvis/tools/apps.py` (`close_app` → `close_background(app)`, если окна нет)
- Test: `tests/test_processes.py`, `tests/test_tools.py`

**Interfaces:**
- Produces (`processes.py`):
  - `@dataclass(frozen=True) class ProcessInfo: pid: int; ppid: int; exe: str; names: tuple[str, ...]; has_window: bool`
    — `names`: имя exe без расширения, FileDescription, ProductName, имя папки exe с `_`/`-` → пробел
  - `PROTECTED_NAMES = {"explorer", "ollama", "ollama app", "llama-server", "jarvis"}`
  - `find_targets(app: str, processes: list[ProcessInfo], protected_pids: set[int]) -> list[ProcessInfo]` — исключить защищённые (`exe` под `C:\Windows`, имя exe в `PROTECTED_NAMES`, pid в `protected_pids`); лучшее совпадение `find_best` по всем `names`; вернуть все процессы с тем же `exe`, корневые (родитель — не этот exe) первыми; нет совпадения — `[]`
  - `class ProcessesBackend(Protocol): processes() -> list[ProcessInfo]; request_close(pid: int) -> None; is_alive(pid: int) -> bool; kill(pid: int) -> None` (`kill` бросает `PermissionError` при нехватке прав)
  - `CLOSE_WAIT_SECONDS = 3`
  - `close_program(app: str, backend: ProcessesBackend, protected_pids: set[int], sleep=time.sleep, clock=time.monotonic) -> ToolResult | None` — `None`, если не найдено; иначе `ToolResult(ok=True, speech="Закрываю {name}.")`, где `name` — лучшее из `names` корневого процесса (описание, если есть)
  - `class WindowsProcesses` — psutil (только процессы текущего пользователя), версия файла через `GetFileVersionInfoW`/`VerQueryValueW` с кэшем по пути, `has_window` через `winapi.visible_windows()`, `request_close` → `post_close` во все `process_windows(pid)`, `kill` → `psutil.Process(pid).kill()` (`AccessDenied` → `PermissionError`, `NoSuchProcess` — молча)
  - `protected_pids()` — свой pid и все родители (`psutil.Process().parents()`)

- [ ] **Step 1: Тесты**

```python
STEAM = ProcessInfo(10, 1, r"D:\Steam\steam.exe", ("steam", "Steam"), False)
HELPER = ProcessInfo(11, 10, r"D:\Steam\bin\steamwebhelper.exe", ("steamwebhelper", "Steam Client WebHelper"), False)
WALLPAPER = ProcessInfo(20, 1, r"C:\SteamLibrary\steamapps\common\wallpaper_engine\wallpaper64.exe", ("wallpaper64", "wallpaper engine"), False)
EXPLORER = ProcessInfo(30, 1, r"C:\Windows\explorer.exe", ("explorer", "Проводник"), True)

def test_find_targets_by_description_and_folder():  # «Steam» → [STEAM]; «Wallpaper Engine» → [WALLPAPER]
def test_find_targets_groups_same_exe_root_first():  # два steam.exe (pid 10 → дочерний pid 12) → [10, 12]
def test_protected_processes_are_never_targets():  # «проводник» → []; pid в protected_pids → []
def test_close_program_kills_only_after_graceful_close_fails():  # процесс жив после ожидания → request_close([10]) затем kill([10])
def test_close_program_does_not_kill_when_process_exits():  # is_alive → False после request_close → kill не вызывался
def test_close_program_never_kills_process_with_window():  # has_window=True → только request_close, ok=True
def test_close_program_reports_permission_error():  # kill → PermissionError → ok=False, error про права
def test_close_program_unknown_returns_none():
```
В `tests/test_tools.py`: `test_close_app_falls_back_to_background_program` — окна «Steam» нет → `close_background("стим")` вызван, его результат возвращён.

- [ ] **Step 2:** `uv run pytest tests/test_processes.py tests/test_tools.py -q` → FAIL.
- [ ] **Step 3:** Реализовать. Ожидание после `request_close` — опрос `is_alive` каждые 0.2 с до `CLOSE_WAIT_SECONDS`.
- [ ] **Step 4:** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** (только по просьбе пользователя).

### Task 5: Подключение, модель и проверка на настоящей модели

**Files:**
- Modify: `src/jarvis/tools/__init__.py` (`build_registry` создаёт `LaunchLog`, `WindowsFiles`, `WindowsInput`, `WindowsProcesses`; регистрирует `files`, `keys`; передаёт в `apps.register` `files`, `launched`, `close_background=lambda app: close_program(app, processes, protected_pids())`)
- Modify: `tests/commands.yaml`, `tests/dialogs.yaml`, при необходимости `src/jarvis/prompts.py`
- Modify: `README.md` (таблица «Что умеет»)

- [ ] **Step 1: Команды и диалоги для модели**

`commands.yaml`:
```yaml
- say: Открой отчёт
  expect: {tool: open_file, args: {name: отчёт}}
- say: Открой файл список покупок
  expect: {tool: open_file, args: {name: список покупок}}
- say: Запусти Warframe
  expect: {tool: open_app, args: {app: Warframe}}
- say: Напечатай привет, как дела
  expect: {tool: type_text, args: {text: привет, как дела}}
- say: Сохрани
  expect: {tool: press_keys, args: {keys: ctrl+s}}
- say: Нажми энтер
  expect: {tool: press_keys, args: {keys: enter}}
- say: Закрой Steam
  expect: {tool: close_app, args: {app: Steam}}
```
`dialogs.yaml`:
```yaml
- dialog: ["Открой отчёт", "Напиши в нём: совещание в пятницу"]
  expect: {tool: type_text, args: {text: совещание в пятницу, window: отчёт}}
- dialog: ["Открой блокнот", "Напиши список покупок: хлеб, молоко", "Сохрани"]
  expect: {tool: press_keys, args: {keys: ctrl+s}}
```
- [ ] **Step 2:** `uv run pytest -m llm -q` → новые случаи, вероятно, FAIL до подключения.
- [ ] **Step 3:** Подключить в `build_registry`; описания инструментов — короткие, с примером фразы. Если модель путает `open_file`/`open_app` или не передаёт `window` — одна строка в промпте на инструмент, с проверкой, что старые случаи не просели.
- [ ] **Step 4:** `uv run pytest -q` → PASS; `uv run pytest -m llm -q` → провалы только «Включи звук обратно» (и не больше одного нестабильного нового, с объяснением).
- [ ] **Step 5: Проверка на настоящей системе** — скрипт в scratchpad (не в репозитории):
  1. создать `jarvis_test.txt` в scratchpad, открыть `WindowsFiles().open(path)`;
  2. через инструменты `type_text(text="Привет, Ёжик!\nВторая строка", window="jarvis_test")` и `press_keys("ctrl+s", window="jarvis_test")`;
  3. прочитать файл с диска — содержимое совпадает; закрыть окно `close_app("jarvis_test")`;
  4. скопировать `C:\Windows\System32\timeout.exe` в scratchpad как `jarvis_tray_test.exe`, запустить без окна, `close_app("jarvis tray test")` → процесс завершён через ~3 с;
  5. убедиться, что Jarvis не реагирует на `press_keys("ctrl+s")` как на горячую клавишу (в логе нет записи команды).
- [ ] **Step 6:** README: строки таблицы для `open_file`, `type_text`, `press_keys`, обновлённого `close_app`.
- [ ] **Step 7: Commit** (только по просьбе пользователя).
