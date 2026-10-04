import sys
import json
import time
import queue
import threading
import os
import sounddevice as sd
import interception
import win32event
import win32api
from winerror import ERROR_ALREADY_EXISTS
from vosk import Model, KaldiRecognizer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QLineEdit, QHeaderView, QFrame, QAbstractItemView, QSystemTrayIcon, 
    QMenu, QStyle, QSizeGrip, QInputDialog, QMessageBox
)
from PySide6.QtCore import Qt, QPoint, Signal, QObject, QTimer
from PySide6.QtGui import QKeySequence, QAction

PROFILES_FILE = "profiles.json"
MODEL_PATH = "model"

KEY_ALIASES = {
    "control": "ctrl", "ctrl": "ctrl", "shift": "shift", "alt": "alt",
    "return": "enter", "enter": "enter", "esc": "escape", "escape": "escape", 
    "space": "space", "-": "minus", "=": "equals"
}

SCANCODE_MAP = {
    16: 'q', 17: 'w', 18: 'e', 19: 'r', 20: 't', 21: 'y', 22: 'u', 23: 'i', 24: 'o', 25: 'p', 26: '[', 27: ']',
    30: 'a', 31: 's', 32: 'd', 33: 'f', 34: 'g', 35: 'h', 36: 'j', 37: 'k', 38: 'l', 39: ';', 40: "'", 41: '`',
    44: 'z', 45: 'x', 46: 'c', 47: 'v', 48: 'b', 49: 'n', 50: 'm', 51: ',', 52: '.', 53: '/',
    2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7', 9: '8', 10: '9', 11: '0', 12: '-', 13: '='
}

QT_KEY_MAP = {
    Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter",
    Qt.Key_Escape: "esc", Qt.Key_Tab: "tab", Qt.Key_Backspace: "backspace", Qt.Key_Delete: "delete",
    Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right",
    Qt.Key_F1: "f1", Qt.Key_F2: "f2", Qt.Key_F3: "f3", Qt.Key_F4: "f4",
    Qt.Key_F5: "f5", Qt.Key_F6: "f6", Qt.Key_F7: "f7", Qt.Key_F8: "f8",
    Qt.Key_F9: "f9", Qt.Key_F10: "f10", Qt.Key_F11: "f11", Qt.Key_F12: "f12",
}

RU_TO_EN = str.maketrans("йцукенгшщзхъфывапролджэячсмитьбюё", "qwertyuiop[]asdfghjkl;'zxcvbnm,.`")

INTERCEPTION_INITIALIZED = False


class SignalBridge(QObject):
    phrase_detected = Signal(str, str)
    status_changed = Signal(str, str)


class GameOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | 
            Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.box = QFrame()
        self.box.setStyleSheet("""
            QFrame {
                background-color: rgba(18, 20, 23, 0.88);
                border: 1px solid #10B981; border-radius: 10px; padding: 10px 16px;
            }
            QLabel { color: #FFFFFF; font-family: 'Segoe UI', sans-serif; }
        """)
        box_layout = QVBoxLayout(self.box)
        box_layout.setContentsMargins(6, 4, 6, 4)
        box_layout.setSpacing(2)

        self.lbl_action = QLabel("🗣️ СКИЛЛ")
        self.lbl_action.setStyleSheet("font-size: 13px; font-weight: bold; color: #10B981;")
        self.lbl_keys = QLabel("[КЛАВИША]")
        self.lbl_keys.setStyleSheet("font-size: 12px; color: #CBD5E1;")

        box_layout.addWidget(self.lbl_action)
        box_layout.addWidget(self.lbl_keys)
        layout.addWidget(self.box)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def trigger(self, phrase, keys):
        self.lbl_action.setText(f"⚡ {phrase.upper()}")
        self.lbl_keys.setText(f"Нажато: {keys.upper()}")
        self.adjustSize()

        screen = QApplication.primaryScreen().geometry()
        x = screen.width() - self.width() - 30
        y = 35
        self.move(x, y)

        self.show()
        self.timer.start(1600)


class VoiceThread(threading.Thread):
    def __init__(self, device_id, commands, bridge):
        super().__init__(daemon=True)
        self.device_id = device_id
        self.commands = commands
        self.bridge = bridge
        self.running = True
        self.audio_queue = queue.Queue()
        self.interception_ready = False
        
        global INTERCEPTION_INITIALIZED
        try:
            if not INTERCEPTION_INITIALIZED:
                interception.auto_capture_devices(keyboard=True, mouse=False)
                INTERCEPTION_INITIALIZED = True
            self.interception_ready = True
        except Exception as e:
            print(f"Ошибка Interception: {e}")

    def callback(self, indata, frames, time_info, status):
        self.audio_queue.put(bytes(indata))

    def run(self):
        if not self.interception_ready:
            self.bridge.status_changed.emit("Ошибка драйвера Interception!", "error")
            return

        try:
            model = Model(MODEL_PATH)
        except Exception:
            self.bridge.status_changed.emit("Папка 'model' не найдена!", "error")
            return

        words = list(self.commands.keys())
        if not words:
            self.bridge.status_changed.emit("Добавьте хотя бы одну команду!", "error")
            return

        grammar = json.dumps(words + ["[unk]"], ensure_ascii=False)
        rec = KaldiRecognizer(model, 16000, grammar)

        try:
            with sd.RawInputStream(
                samplerate=16000, blocksize=4000, device=self.device_id,
                dtype="int16", channels=1, callback=self.callback
            ):
                self.bridge.status_changed.emit("Слушаю команды...", "active")
                while self.running:
                    data = self.audio_queue.get()
                    if rec.AcceptWaveform(data):
                        res = json.loads(rec.Result())
                        text = res.get("text", "").strip()
                        if text and text in self.commands:
                            action_payload = self.commands[text]
                            self.bridge.phrase_detected.emit(text, action_payload)
                            self.execute_action(action_payload)
        except Exception as e:
            self.bridge.status_changed.emit(f"Ошибка аудио: {e}", "error")

    def execute_action(self, payload):
        steps = [s.strip() for s in payload.split(",") if s.strip()]
        for step in steps:
            self.press_single_combo(step)
            time.sleep(0.1)

    def press_single_combo(self, combo_str):
        try:
            combo_str = combo_str.lower().translate(RU_TO_EN)
            keys = [k.strip() for k in combo_str.split("+") if k.strip()]
            mapped_keys = [KEY_ALIASES.get(k, k) for k in keys]

            for k in mapped_keys:
                interception.key_down(k)
                time.sleep(0.02)
            
            time.sleep(0.15) 
            
            for k in reversed(mapped_keys):
                interception.key_up(k)
                time.sleep(0.02)
                
        except Exception as err:
            self.bridge.status_changed.emit(f"Сбой кнопки: {err}", "error")

    def stop(self):
        self.running = False


class KeyCaptureLineEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Кликни и нажми")
        self.setReadOnly(True)
        self.setCursor(Qt.PointingHandCursor)
        self.capturing = False

    def mousePressEvent(self, event):
        self.capturing = True
        self.setText("")
        self.setPlaceholderText("Жду нажатия...")
        self.setStyleSheet("border: 1px solid #3B82F6; background-color: #1E293B;")
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        self.capturing = False
        self.setStyleSheet("")
        if not self.text():
            self.setPlaceholderText("Кликни и нажми")
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if not self.capturing:
            return

        key = event.key()

        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return

        if key in (Qt.Key_Backspace, Qt.Key_Delete) and not event.modifiers():
            self.clear()
            self.capturing = False
            self.setStyleSheet("")
            self.clearFocus()
            self.setPlaceholderText("Кликни и нажми")
            return

        modifiers = event.modifiers()
        parts = []

        if modifiers & Qt.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.AltModifier:
            parts.append("alt")
        if modifiers & Qt.ShiftModifier:
            parts.append("shift")

        scan_code = event.nativeScanCode()

        if scan_code in SCANCODE_MAP:
            parts.append(SCANCODE_MAP[scan_code])
        elif key in QT_KEY_MAP:
            parts.append(QT_KEY_MAP[key])
        else:
            text = QKeySequence(key).toString().lower().translate(RU_TO_EN)
            if text:
                parts.append(text)

        combo = "+".join(parts)
        self.setText(combo)
        
        self.capturing = False
        self.setStyleSheet("")
        self.clearFocus()


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(600, 750)
        self.setMinimumSize(450, 550)

        self.bridge = SignalBridge()
        self.bridge.phrase_detected.connect(self.on_phrase)
        self.bridge.status_changed.connect(self.on_status)

        self.overlay = GameOverlay()
        self.drag_position = QPoint()
        self.voice_thread = None
        
        self.profiles = self.load_profiles()
        self.current_profile = list(self.profiles.keys())[0] if self.profiles else "Основной"

        self.apply_styles()
        self.init_ui()
        self.init_tray()

    def apply_styles(self):
        self.setStyleSheet("""
            QWidget#MainContainer { background-color: #121417; border: 1px solid #282C34; border-radius: 12px; }
            QWidget { color: #E2E8F0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
            QFrame.card { background-color: #1A1D24; border: 1px solid #282C34; border-radius: 10px; }
            QLabel.sectionTitle { font-size: 11px; font-weight: 700; color: #8E96A4; letter-spacing: 0.5px; text-transform: uppercase; }
            QComboBox, QLineEdit { background-color: #222630; border: 1px solid #333842; border-radius: 6px; padding: 8px 12px; color: #FFFFFF; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #1A1D24; selection-background-color: #3B82F6; color: #FFFFFF; border: 1px solid #333842; }
            QTableWidget { background-color: #1A1D24; border: 1px solid #282C34; border-radius: 8px; gridline-color: #222630; color: #FFFFFF; outline: 0; }
            QTableWidget::item:selected { background-color: rgba(59, 130, 246, 0.2); color: #FFFFFF; border: 1px solid #3B82F6; }
            QHeaderView::section { background-color: #15181E; color: #8E96A4; padding: 6px; border: none; font-weight: 600; }
            QPushButton { font-weight: 600; border-radius: 6px; padding: 8px 14px; background-color: #222630; border: 1px solid #333842; color: #CBD5E1; }
            QPushButton:hover { background-color: #2A2F3D; color: #FFFFFF; }
            QPushButton.btnPrimary { background-color: #3B82F6; color: white; border: none; }
            QPushButton.btnPrimary:hover { background-color: #2563EB; }
            QPushButton.btnDanger { background-color: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #F87171; }
            QPushButton.btnDanger:hover { background-color: #EF4444; color: white; }
            QPushButton.titleBtn { background: transparent; border: none; color: #94A3B8; font-size: 14px; border-radius: 4px; padding: 2px 8px; }
            QPushButton.titleBtn:hover { background-color: #282C34; color: white; }
            QPushButton.closeBtn:hover { background-color: #EF4444; color: white; }
            QSizeGrip { width: 16px; height: 16px; margin: 2px; }
        """)

    def init_ui(self):
        container = QWidget()
        container.setObjectName("MainContainer")
        self.setCentralWidget(container)

        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(16, 10, 16, 16)
        main_layout.setSpacing(12)

        title_bar = QHBoxLayout()
        app_title = QLabel("🎙 Allods Voice Control")
        app_title.setStyleSheet("font-weight: 700; font-size: 13px; color: #CBD5E1;")
        
        warn_title = QLabel("⚠️ ЗАПУСКАЙТЕ ДО ИГРЫ")
        warn_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #F59E0B;")

        title_bar.addWidget(app_title)
        title_bar.addWidget(warn_title)
        title_bar.addStretch()

        btn_min = QPushButton("🗕")
        btn_min.setProperty("class", "titleBtn")
        btn_min.clicked.connect(self.hide)

        btn_close = QPushButton("✕")
        btn_close.setProperty("class", "titleBtn closeBtn")
        btn_close.clicked.connect(self.hide)

        title_bar.addWidget(btn_min)
        title_bar.addWidget(btn_close)
        main_layout.addLayout(title_bar)

        mic_card = QFrame()
        mic_card.setProperty("class", "card")
        mic_layout = QVBoxLayout(mic_card)
        mic_title = QLabel("ИСТОЧНИК ЗВУКА")
        mic_title.setProperty("class", "sectionTitle")
        self.mic_combo = QComboBox()
        self.populate_mics()
        mic_layout.addWidget(mic_title)
        mic_layout.addWidget(self.mic_combo)
        main_layout.addWidget(mic_card)

        table_card = QFrame()
        table_card.setProperty("class", "card")
        table_layout = QVBoxLayout(table_card)
        
        prof_layout = QHBoxLayout()
        prof_layout.addWidget(QLabel("ПРОФИЛЬ:"), 0)
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(self.profiles.keys())
        self.profile_combo.setCurrentText(self.current_profile)
        self.profile_combo.currentTextChanged.connect(self.on_profile_changed)
        prof_layout.addWidget(self.profile_combo, 1)

        btn_add_prof = QPushButton("+")
        btn_add_prof.clicked.connect(self.add_profile)
        btn_del_prof = QPushButton("-")
        btn_del_prof.clicked.connect(self.del_profile)
        
        prof_layout.addWidget(btn_add_prof)
        prof_layout.addWidget(btn_del_prof)
        table_layout.addLayout(prof_layout)

        table_title = QLabel("БИНДЫ (Кликните для редактирования)")
        table_title.setProperty("class", "sectionTitle")
        table_layout.addWidget(table_title)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Слово / Фраза", "Клавиша"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemClicked.connect(self.on_table_item_clicked)
        
        table_layout.addWidget(self.table)
        self.refresh_table()

        inputs_layout = QHBoxLayout()
        self.input_word = QLineEdit()
        self.input_word.setPlaceholderText("Слово (щит, стан)")
        
        self.input_key = KeyCaptureLineEdit()

        btn_add = QPushButton("Сохранить")
        btn_add.setProperty("class", "btnPrimary")
        btn_add.clicked.connect(self.add_command)

        inputs_layout.addWidget(self.input_word, 4)
        inputs_layout.addWidget(self.input_key, 4)
        inputs_layout.addWidget(btn_add, 3)
        table_layout.addLayout(inputs_layout)

        btn_del = QPushButton("Удалить выбранную команду")
        btn_del.setProperty("class", "btnDanger")
        btn_del.clicked.connect(self.del_command)
        table_layout.addWidget(btn_del)
        main_layout.addWidget(table_card)

        ctrl_card = QFrame()
        ctrl_card.setProperty("class", "card")
        ctrl_layout = QVBoxLayout(ctrl_card)

        status_row = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #64748B; font-size: 15px;")
        self.status_text = QLabel("Готов к работе")
        self.status_text.setStyleSheet("color: #94A3B8; font-weight: 600;")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_text)
        status_row.addStretch()
        ctrl_layout.addLayout(status_row)

        self.btn_toggle = QPushButton("СТАРТ")
        self.btn_toggle.setFixedHeight(44)
        self.btn_toggle.setStyleSheet("""
            QPushButton { background-color: #10B981; color: white; font-size: 14px; font-weight: 700; border: none; border-radius: 8px; }
            QPushButton:hover { background-color: #059669; }
        """)
        self.btn_toggle.clicked.connect(self.toggle_listening)
        ctrl_layout.addWidget(self.btn_toggle)

        self.log_label = QLabel("Последнее действие: —")
        self.log_label.setStyleSheet("color: #64748B; font-size: 11px; margin-top: 4px;")
        ctrl_layout.addWidget(self.log_label)
        main_layout.addWidget(ctrl_card)

        # Ползунок для изменения размера в правом нижнем углу
        size_grip_layout = QHBoxLayout()
        size_grip_layout.addStretch()
        self.size_grip = QSizeGrip(self)
        size_grip_layout.addWidget(self.size_grip)
        main_layout.addLayout(size_grip_layout)

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        # Стандартная иконка, чтобы трей не был пустым квадратом
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        
        self.tray_menu = QMenu()
        
        self.action_toggle = QAction("Включить микрофон", self)
        self.action_toggle.triggered.connect(self.toggle_listening)
        self.tray_menu.addAction(self.action_toggle)
        
        self.tray_menu.addSeparator()
        
        self.action_show = QAction("Показать окно", self)
        self.action_show.triggered.connect(self.show_window)
        self.tray_menu.addAction(self.action_show)
        
        self.action_quit = QAction("Выход из программы", self)
        self.action_quit.triggered.connect(self.quit_app)
        self.tray_menu.addAction(self.action_quit)
        
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def show_window(self):
        self.showNormal()
        self.activateWindow()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_window()

    def quit_app(self):
        if self.voice_thread and self.voice_thread.is_alive():
            self.voice_thread.stop()
        self.overlay.close()
        self.tray_icon.hide()
        QApplication.quit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and not self.drag_position.isNull():
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def populate_mics(self):
        devices = sd.query_devices()
        default_in = sd.default.device[0]
        added_names = set()
        for idx, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                name = dev['name']
                if name not in added_names:
                    added_names.add(name)
                    is_def = " (По умолчанию)" if idx == default_in else ""
                    self.mic_combo.addItem(f"{name}{is_def}", idx)

    def load_profiles(self):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"Основной": {"стан": "e", "щит": "ctrl+f1"}}

    def save_profiles(self):
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(self.profiles, f, ensure_ascii=False, indent=2)

    def on_profile_changed(self, text):
        if text and text in self.profiles:
            self.current_profile = text
            self.refresh_table()

    def add_profile(self):
        name, ok = QInputDialog.getText(self, "Новый профиль", "Название профиля:")
        if ok and name:
            if name not in self.profiles:
                self.profiles[name] = {}
                self.profile_combo.addItem(name)
                self.profile_combo.setCurrentText(name)
                self.save_profiles()

    def del_profile(self):
        if len(self.profiles) > 1:
            name = self.current_profile
            reply = QMessageBox.question(self, "Удаление", f"Удалить профиль '{name}'?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                del self.profiles[name]
                self.profile_combo.removeItem(self.profile_combo.currentIndex())
                self.save_profiles()
        else:
            QMessageBox.warning(self, "Ошибка", "Нельзя удалить последний профиль!")

    def refresh_table(self):
        self.table.setRowCount(0)
        commands = self.profiles.get(self.current_profile, {})
        for row, (w, k) in enumerate(commands.items()):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(w))
            self.table.setItem(row, 1, QTableWidgetItem(k))

    def on_table_item_clicked(self, item):
        row = item.row()
        word = self.table.item(row, 0).text()
        key = self.table.item(row, 1).text()
        self.input_word.setText(word)
        self.input_key.setText(key)

    def add_command(self):
        w = self.input_word.text().strip().lower()
        k = self.input_key.text().strip().lower()
        if not w or not k:
            return
        self.profiles[self.current_profile][w] = k
        self.save_profiles()
        self.refresh_table()
        self.input_word.clear()
        self.input_key.clear()

    def del_command(self):
        curr = self.table.currentRow()
        if curr >= 0:
            w = self.table.item(curr, 0).text()
            if w in self.profiles[self.current_profile]:
                del self.profiles[self.current_profile][w]
                self.save_profiles()
                self.refresh_table()
                self.input_word.clear()
                self.input_key.clear()

    def toggle_listening(self):
        if self.voice_thread and self.voice_thread.is_alive():
            self.voice_thread.stop()
            self.btn_toggle.setText("СТАРТ")
            self.btn_toggle.setStyleSheet("""
                QPushButton { background-color: #10B981; color: white; font-size: 14px; font-weight: 700; border: none; border-radius: 8px; }
                QPushButton:hover { background-color: #059669; }
            """)
            self.on_status("Остановлено", "idle")
            self.action_toggle.setText("Включить микрофон")
            self.profile_combo.setEnabled(True)
        else:
            dev_idx = self.mic_combo.currentData()
            active_commands = self.profiles[self.current_profile]
            
            self.voice_thread = VoiceThread(dev_idx, active_commands, self.bridge)
            self.voice_thread.start()
            
            self.btn_toggle.setText("ОСТАНОВИТЬ")
            self.btn_toggle.setStyleSheet("""
                QPushButton { background-color: #EF4444; color: white; font-size: 14px; font-weight: 700; border: none; border-radius: 8px; }
                QPushButton:hover { background-color: #DC2626; }
            """)
            self.action_toggle.setText("Выключить микрофон")
            self.profile_combo.setEnabled(False) # Блокируем смену профиля во время работы

    def on_phrase(self, phrase, key):
        self.log_label.setText(f"Последнее действие: Сказано «{phrase}» ➔ нажато [{key}]")
        self.overlay.trigger(phrase, key)

    def on_status(self, text, state):
        self.status_text.setText(text)
        if state == "active":
            self.status_dot.setStyleSheet("color: #10B981; font-size: 15px;")
            self.status_text.setStyleSheet("color: #10B981; font-weight: 600;")
        elif state == "error":
            self.status_dot.setStyleSheet("color: #EF4444; font-size: 15px;")
            self.status_text.setStyleSheet("color: #EF4444; font-weight: 600;")
        else:
            self.status_dot.setStyleSheet("color: #64748B; font-size: 15px;")
            self.status_text.setStyleSheet("color: #94A3B8; font-weight: 600;")

    def closeEvent(self, event):
        # Вместо закрытия сворачиваем в трей
        event.ignore()
        self.hide()
        self.tray_icon.showMessage(
            "Allods Voice Control", 
            "Программа работает в фоновом режиме", 
            QSystemTrayIcon.Information, 
            2000
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Создаем системный замок (Mutex). Если он уже существует - значит прога работает
    mutex = win32event.CreateMutex(None, False, "AVC_SingleInstance_Mutex")
    if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
        QMessageBox.warning(None, "Блокировка", "Программа уже запущена!\nПроверь системный трей (рядом с часами).")
        sys.exit(0)
    
    app.setQuitOnLastWindowClosed(False)
    window = App()
    window.show()
    sys.exit(app.exec())