"""Desktop GUI (PySide6) for synchronising TimeShot videos with XDF audio.

Workflow: open the LabRecorder ``.xdf`` file, add the video files (or drop
them on the window), check the automatic stream pairing, then export either
one synchronised video per camera or a mosaic of all of them.

Loading and exporting run on a worker thread; the window stays responsive
and every job can be cancelled.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .pipeline import (
    Cancelled,
    ProgressCallback,
    RenderOptions,
    SpanMode,
    VideoInput,
    default_output_fps,
    export_mosaic,
    export_synced_video,
    match_videos,
)
from .video import EncoderSettings
from .xdf_streams import XdfRecording, load_recording

log = logging.getLogger("moviemerger")

VIDEO_SUFFIXES = {".avi", ".mp4", ".mkv", ".mov", ".m4v", ".wmv"}
VIDEO_FILTER = "Videos (" + " ".join(f"*{s}" for s in sorted(VIDEO_SUFFIXES)) + ");;All files (*)"
NO_STREAM = "(not paired)"
NO_AUDIO = "(no audio)"


# ── background work ──────────────────────────────────────────────────────


class _Worker(QObject):
    """Runs one job function on a QThread and reports back via signals.

    The job receives a progress callback and a cancel event; raising
    :class:`Cancelled` ends it quietly.
    """

    progress = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, job: Callable[[ProgressCallback, threading.Event], Any]) -> None:
        super().__init__()
        self._job = job
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result = self._job(self.progress.emit, self.cancel_event)
        except Cancelled:
            self.cancelled.emit()
        except Exception as error:  # reported to the user, never swallowed
            log.exception("Job failed")
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)


class _LogBridge(QObject, logging.Handler):
    """Forwards log records to the GUI thread as text."""

    message = Signal(str)

    def __init__(self) -> None:
        QObject.__init__(self)
        logging.Handler.__init__(self, level=logging.INFO)
        self.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.message.emit(self.format(record))


# ── video table model ────────────────────────────────────────────────────


@dataclass
class _VideoRow:
    """One row of the video table, independent of its widgets."""

    path: Path
    stream: str | None = None
    label: str = ""
    latency_ms: float = 0.0


class _Column:
    FILE, STREAM, LABEL, LATENCY = range(4)
    HEADERS = ("Video file", "Marker stream", "Label (mosaic)", "Latency (ms)")


# ── main window ──────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    """The MovieMerger main window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"MovieMerger {__version__}")
        self.setAcceptDrops(True)
        self.resize(980, 760)

        self._settings = QSettings("markspan", "MovieMerger")
        self._recording: XdfRecording | None = None
        self._rows: list[_VideoRow] = []
        self._thread: QThread | None = None
        self._worker: _Worker | None = None

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self._build_recording_group())
        layout.addWidget(self._build_video_group(), stretch=1)
        layout.addWidget(self._build_output_group())
        layout.addLayout(self._build_action_row())
        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(False)
        self._status = QLabel("Open an XDF file and add the videos TimeShot recorded.")
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        self._log = QPlainTextEdit(readOnly=True)
        self._log.setMaximumBlockCount(2000)
        layout.addWidget(self._log, stretch=1)
        self.setCentralWidget(central)

        self._log_bridge = _LogBridge()
        self._log_bridge.message.connect(self._log.appendPlainText)
        package_log = logging.getLogger("moviemerger")
        if package_log.level == logging.NOTSET:
            package_log.setLevel(logging.INFO)
        package_log.addHandler(self._log_bridge)
        self._update_enabled()

    # ── construction ─────────────────────────────────────────────────────

    def _build_recording_group(self) -> QGroupBox:
        group = QGroupBox("Recording")
        grid = QGridLayout(group)
        self._xdf_edit = QLineEdit(readOnly=True, placeholderText="LabRecorder .xdf file")
        browse = QPushButton("Open XDF…")
        browse.clicked.connect(self._choose_xdf)
        self._audio_combo = QComboBox()
        self._audio_combo.setMinimumWidth(220)
        self._audio_latency = _milliseconds_spin("Milliseconds by which the audio timestamps lag the sound.")
        grid.addWidget(QLabel("XDF file:"), 0, 0)
        grid.addWidget(self._xdf_edit, 0, 1, 1, 3)
        grid.addWidget(browse, 0, 4)
        grid.addWidget(QLabel("Audio stream:"), 1, 0)
        grid.addWidget(self._audio_combo, 1, 1)
        grid.addWidget(QLabel("Audio latency:"), 1, 2, alignment=Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self._audio_latency, 1, 3)
        grid.setColumnStretch(1, 1)
        return group

    def _build_video_group(self) -> QGroupBox:
        group = QGroupBox("Videos (mosaic order: left to right, top to bottom)")
        box = QHBoxLayout(group)
        self._table = QTableWidget(0, len(_Column.HEADERS))
        self._table.setHorizontalHeaderLabels(_Column.HEADERS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_Column.FILE, QHeaderView.ResizeMode.Stretch)
        for column in (_Column.STREAM, _Column.LABEL, _Column.LATENCY):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        box.addWidget(self._table, stretch=1)

        buttons = QVBoxLayout()
        for text, slot in (
            ("Add videos…", self._choose_videos),
            ("Remove", self._remove_selected),
            ("Move up", lambda: self._move_selected(-1)),
            ("Move down", lambda: self._move_selected(1)),
            ("Auto-pair", self._auto_pair),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch()
        box.addLayout(buttons)
        return group

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("Output")
        form = QFormLayout(group)

        self._fps = QDoubleSpinBox(decimals=2, minimum=0.0, maximum=240.0, singleStep=1.0)
        self._fps.setSpecialValueText("Auto")
        self._fps.setToolTip("Constant output frame rate. Auto uses the fastest camera's typical rate.")
        self._span = QComboBox()
        self._span.addItem("Everything (fill gaps with black / silence)", SpanMode.UNION)
        self._span.addItem("Only where all streams recorded", SpanMode.OVERLAP)
        self._quality = QSpinBox(minimum=0, maximum=40, value=18)
        self._quality.setToolTip("x264 CRF: lower is better quality and larger files. 18 is visually lossless.")

        self._mosaic_width = QSpinBox(minimum=320, maximum=7680, singleStep=160, value=1920, suffix=" px")
        self._columns = QSpinBox(minimum=0, maximum=16)
        self._columns.setSpecialValueText("Auto")
        self._labels = QCheckBox("Caption each tile", checked=True)
        mosaic_row = QHBoxLayout()
        for widget in (QLabel("Width"), self._mosaic_width, QLabel("Columns"), self._columns, self._labels):
            mosaic_row.addWidget(widget)
        mosaic_row.addStretch()

        self._out_dir = QLineEdit(placeholderText="Next to the videos")
        out_browse = QPushButton("Browse…")
        out_browse.clicked.connect(self._choose_out_dir)
        out_row = QHBoxLayout()
        out_row.addWidget(self._out_dir, stretch=1)
        out_row.addWidget(out_browse)

        form.addRow("Frame rate:", self._fps)
        form.addRow("Time span:", self._span)
        form.addRow("Quality (CRF):", self._quality)
        form.addRow("Mosaic:", mosaic_row)
        form.addRow("Output folder:", out_row)
        return group

    def _build_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._sync_button = QPushButton("Export synced videos")
        self._sync_button.clicked.connect(self._export_synced)
        self._mosaic_button = QPushButton("Export mosaic…")
        self._mosaic_button.clicked.connect(self._export_mosaic)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.clicked.connect(self._cancel)
        row.addStretch()
        for button in (self._sync_button, self._mosaic_button, self._cancel_button):
            row.addWidget(button)
        return row

    # ── recording ────────────────────────────────────────────────────────

    def _choose_xdf(self) -> None:
        start = self._settings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Open XDF recording", start, "XDF files (*.xdf);;All files (*)")
        if path:
            self._load_xdf(Path(path))

    def _load_xdf(self, path: Path) -> None:
        self._settings.setValue("last_dir", str(path.parent))
        self._xdf_edit.setText(str(path))
        self._recording = None
        self._run(lambda progress, cancel: load_recording(path), self._on_xdf_loaded, f"Reading {path.name}…")

    def _on_xdf_loaded(self, recording: XdfRecording) -> None:
        self._recording = recording
        self._audio_combo.clear()
        for audio in recording.audio:
            seconds = audio.time_stamps[-1] - audio.time_stamps[0]
            self._audio_combo.addItem(f"{audio.name}  ({audio.sample_rate:.0f} Hz, {seconds:.0f} s)", audio.name)
        self._audio_combo.addItem(NO_AUDIO, None)
        if recording.cameras:
            fps = default_output_fps(recording.cameras)
            self._fps.setSpecialValueText(f"Auto ({fps:g})")
        self._sync_rows_from_table()
        self._auto_pair()
        self._set_status(f"{recording.path.name}: {len(recording.cameras)} camera(s), {len(recording.audio)} audio.")

    # ── video table ──────────────────────────────────────────────────────

    def _choose_videos(self) -> None:
        start = self._settings.value("last_dir", str(Path.home()))
        paths, _ = QFileDialog.getOpenFileNames(self, "Add videos", start, VIDEO_FILTER)
        self._add_videos([Path(p) for p in paths])

    def _add_videos(self, paths: list[Path]) -> None:
        self._sync_rows_from_table()
        known = {row.path for row in self._rows}
        new = [p for p in paths if p not in known]
        if not new:
            return
        self._settings.setValue("last_dir", str(new[0].parent))
        self._rows.extend(_VideoRow(path=p) for p in new)
        self._auto_pair(only_unpaired=True)

    def _remove_selected(self) -> None:
        self._sync_rows_from_table()
        selected = {index.row() for index in self._table.selectionModel().selectedRows()}
        self._rows = [row for i, row in enumerate(self._rows) if i not in selected]
        self._refresh_table()

    def _move_selected(self, step: int) -> None:
        self._sync_rows_from_table()
        current = self._table.currentRow()
        target = current + step
        if current < 0 or not 0 <= target < len(self._rows):
            return
        self._rows[current], self._rows[target] = self._rows[target], self._rows[current]
        self._refresh_table()
        self._table.selectRow(target)

    def _auto_pair(self, only_unpaired: bool = False) -> None:
        """Pair videos with marker streams (see :func:`match_videos`)."""
        self._sync_rows_from_table()
        if self._recording is not None:
            rows = [r for r in self._rows if not (only_unpaired and r.stream)]
            taken = {r.stream for r in self._rows if only_unpaired and r.stream}
            cameras = [c for c in self._recording.cameras if c.name not in taken]
            for row, camera in zip(rows, match_videos([r.path for r in rows], cameras)):
                row.stream = camera.name if camera else None
        self._refresh_table()

    def _refresh_table(self) -> None:
        cameras = [c.name for c in self._recording.cameras] if self._recording else []
        self._table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            file_item = QTableWidgetItem(row.path.name)
            file_item.setToolTip(str(row.path))
            file_item.setFlags(file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(i, _Column.FILE, file_item)

            combo = QComboBox()
            combo.addItem(NO_STREAM, None)
            for name in cameras:
                combo.addItem(name, name)
            combo.setCurrentIndex(max(0, combo.findData(row.stream)))
            combo.currentIndexChanged.connect(self._update_enabled)
            self._table.setCellWidget(i, _Column.STREAM, combo)

            label_item = QTableWidgetItem(row.label)
            label_item.setToolTip("Leave empty to use the camera name from the XDF.")
            self._table.setItem(i, _Column.LABEL, label_item)

            latency = _milliseconds_spin("Milliseconds by which this camera's frame markers lag the capture.")
            latency.setValue(row.latency_ms)
            self._table.setCellWidget(i, _Column.LATENCY, latency)
        self._update_enabled()

    def _sync_rows_from_table(self) -> None:
        """Copy edits made in the table widgets back into ``self._rows``."""
        for i, row in enumerate(self._rows[: self._table.rowCount()]):
            combo = self._table.cellWidget(i, _Column.STREAM)
            if isinstance(combo, QComboBox):
                row.stream = combo.currentData()
            label = self._table.item(i, _Column.LABEL)
            row.label = label.text().strip() if label else ""
            latency = self._table.cellWidget(i, _Column.LATENCY)
            if isinstance(latency, QDoubleSpinBox):
                row.latency_ms = latency.value()

    # ── export ───────────────────────────────────────────────────────────

    def _choose_out_dir(self) -> None:
        start = self._out_dir.text() or self._settings.value("last_dir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Output folder", start)
        if folder:
            self._out_dir.setText(folder)

    def _video_inputs(self) -> list[VideoInput] | None:
        """Paired videos, or None (after telling the user) if any is unpaired."""
        assert self._recording is not None
        self._sync_rows_from_table()
        unpaired = [row.path.name for row in self._rows if row.stream is None]
        if unpaired:
            QMessageBox.warning(self, "Unpaired videos", "Pick a marker stream for:\n" + "\n".join(unpaired))
            return None
        return [
            VideoInput(
                path=row.path,
                stream=self._recording.camera(row.stream),
                label=row.label or None,
                latency=row.latency_ms / 1000.0,
            )
            for row in self._rows
        ]

    def _options(self) -> RenderOptions:
        return RenderOptions(
            fps=self._fps.value() or None,
            span_mode=self._span.currentData(),
            audio_latency=self._audio_latency.value() / 1000.0,
            labels=self._labels.isChecked(),
            mosaic_width=self._mosaic_width.value(),
            columns=self._columns.value() or None,
            encoder=EncoderSettings(crf=self._quality.value()),
        )

    def _selected_audio(self):
        name = self._audio_combo.currentData()
        return self._recording.audio_stream(name) if self._recording and name else None

    def _output_folder(self, video: Path) -> Path:
        text = self._out_dir.text().strip()
        return Path(text) if text else video.parent

    def _export_synced(self) -> None:
        videos = self._video_inputs()
        if not videos:
            return
        audio, options = self._selected_audio(), self._options()
        outputs = [self._output_folder(v.path) / f"{v.path.stem}_synced.mp4" for v in videos]
        if not self._confirm_overwrite(outputs):
            return

        def job(progress: ProgressCallback, cancel: threading.Event) -> list[Path]:
            for i, (video, output) in enumerate(zip(videos, outputs)):

                def scaled(fraction: float, message: str, i: int = i) -> None:
                    progress((i + fraction) / len(videos), message)

                export_synced_video(video, audio, output, options, scaled, cancel)
            return outputs

        self._run(job, self._on_exported, "Exporting synced videos…")

    def _export_mosaic(self) -> None:
        videos = self._video_inputs()
        if not videos:
            return
        default = self._output_folder(videos[0].path) / "mosaic.mp4"
        path, _ = QFileDialog.getSaveFileName(self, "Save mosaic", str(default), "MP4 video (*.mp4)")
        if not path:
            return
        output = Path(path).with_suffix(".mp4")
        audio, options = self._selected_audio(), self._options()
        # QFileDialog already asked about overwriting the chosen file.
        self._run(
            lambda progress, cancel: [export_mosaic(videos, audio, output, options, progress, cancel)],
            self._on_exported,
            "Exporting mosaic…",
        )

    def _confirm_overwrite(self, outputs: list[Path]) -> bool:
        existing = [p.name for p in outputs if p.exists()]
        if not existing:
            return True
        answer = QMessageBox.question(
            self, "Overwrite files?", "These files exist and will be replaced:\n" + "\n".join(existing)
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_exported(self, outputs: list[Path]) -> None:
        self._set_status("Done: " + ", ".join(p.name for p in outputs))

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel_event.set()
            self._set_status("Cancelling…")

    # ── job plumbing ─────────────────────────────────────────────────────

    def _run(
        self, job: Callable[[ProgressCallback, threading.Event], Any], on_success: Callable[[Any], None], status: str
    ) -> None:
        """Run ``job`` on a worker thread; ``on_success`` gets its result."""
        if self._thread is not None:
            return
        self._thread = QThread(self)
        self._worker = _Worker(job)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(lambda: self._set_status("Cancelled."))
        for signal in (self._worker.succeeded, self._worker.failed, self._worker.cancelled):
            signal.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_finished)
        self._progress.setValue(0)
        self._set_status(status)
        self._thread.start()
        self._update_enabled()

    @Slot(float, str)
    def _on_progress(self, fraction: float, message: str) -> None:
        self._progress.setValue(int(fraction * 1000))
        self._status.setText(message)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._set_status("Failed.")
        QMessageBox.critical(self, "MovieMerger", message)

    @Slot()
    def _on_thread_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self._update_enabled()

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def _update_enabled(self) -> None:
        busy = self._thread is not None
        ready = not busy and self._recording is not None and bool(self._rows)
        self._sync_button.setEnabled(ready)
        self._mosaic_button.setEnabled(ready)
        self._cancel_button.setEnabled(busy)

    # ── drag and drop ────────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        xdfs = [p for p in paths if p.suffix.lower() == ".xdf"]
        videos = [p for p in paths if p.suffix.lower() in VIDEO_SUFFIXES]
        if videos:
            self._add_videos(videos)
        if xdfs and self._thread is None:
            self._load_xdf(xdfs[0])

    def closeEvent(self, event) -> None:
        if self._thread is not None:
            if (
                QMessageBox.question(self, "Quit", "An export is running. Cancel it and quit?")
                != QMessageBox.StandardButton.Yes
            ):
                event.ignore()
                return
            self._worker.cancel_event.set()
            self._thread.quit()
            self._thread.wait()
        logging.getLogger("moviemerger").removeHandler(self._log_bridge)
        super().closeEvent(event)


def _milliseconds_spin(tooltip: str) -> QDoubleSpinBox:
    spin = QDoubleSpinBox(decimals=1, minimum=-5000.0, maximum=5000.0, singleStep=5.0, suffix=" ms")
    spin.setToolTip(tooltip + " Positive values move it earlier.")
    return spin


def main() -> int:
    """Start the GUI; returns the application's exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
