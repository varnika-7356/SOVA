# deviceGUI.py
"""
Device GUI (CustomTkinter) with OUTPUT/GPIO behavior.

Modifications:
- Smaller KILL button for a less-dominant appearance.
- Centered team name "S²ICD" (Unicode superscript 2) added in the center area.
"""

import threading
import time
import sys
import traceback

import customtkinter as ctk

# Try to import gpiozero; provide a dummy fallback if not available
try:
    from gpiozero import LED
    GPIO_AVAILABLE = True
except Exception:
    GPIO_AVAILABLE = False

    class LED:  # dummy LED for testing without hardware
        def __init__(self, pin):
            self.pin = pin
            self._state = False

        def on(self):
            self._state = True
            print(f"[DUMMY LED] pin {self.pin} -> ON")

        def off(self):
            self._state = False
            print(f"[DUMMY LED] pin {self.pin} -> OFF")

        def is_lit(self):
            return self._state

# Appearance / constants
ctk.set_appearance_mode("Dark")
PRIMARY_BG = "#060809"
NEON_GREEN = "#00FF9C"
NEON_YELLOW = "#FFC93C"
DANGER_RED = "#FF4D4D"
TOGGLE_BLUE = "#5555FF"
TOGGLE_GREEN = "#22DD22"
TEAM_NEON = "#00FFCC"

# Button sizes
CIRCLE_BTN = 120        # default circle size for START/STOP/OUTPUT
KILL_BTN_SIZE = 80      # reduced kill button size

FONT_STATUS = 34
FONT_ETA = 30

# Timings (seconds)
AUTO_TOTAL = 30.0
MANUAL_FOAM = 5.0
MANUAL_INFLATE = 20.0
MANUAL_STABILIZE = 20 * 60.0

# GPIO cycle parameters
GPIO_HIGH_SEC = 3.0
GPIO_LOW_SEC = 5.0

class SimpleDeviceUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Device — Simple Display")
        self.geometry("780x440")
        self.configure(fg_color=PRIMARY_BG)
        self.resizable(True, True)

        # --- device state for timers ---
        self.killed = False
        self.running = False
        self.paused = False
        self.pause_flag = False
        self.current_stage = None
        self.stage_total = 0.0
        self.stage_remaining = 0.0
        self.manual_state = "idle"
        self.worker = None
        self.current_mode = "Manual"

        # --- GPIO control ---
        # Mode machine for OUTPUT behavior: values -> 'idle', 'steady_high', 'cycle'
        self._gpio_mode = 'idle'  # default: idle (LOW, no cycle)
        self._gpio_mode_lock = threading.Lock()

        try:
            self.led = LED(17)
        except Exception:
            # fallback to dummy (the class above)
            self.led = LED(17)

        # Ensure LOW at startup for safety
        try:
            self.led.off()
            print("[INIT] GPIO17 forced LOW at startup (safe).")
        except Exception as e:
            print("[INIT] Could not force GPIO LOW:", e)

        # Cycle thread control
        self._gpio_cycle_thread = None
        self._gpio_cycle_stop_evt = threading.Event()

        # Build UI and handlers
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        pad = 12

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=pad, pady=(pad, 4))

        # Reduced-size KILL button
        self.kill_btn = ctk.CTkButton(
            top,
            text="KILL",
            width=KILL_BTN_SIZE,
            height=KILL_BTN_SIZE,
            corner_radius=KILL_BTN_SIZE // 2,
            fg_color=DANGER_RED,
            hover_color="#ff3333",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._do_kill,
        )
        self.kill_btn.pack(side="left", padx=(16, 20), pady=4)

        self.mode_var = ctk.StringVar(value="Manual")
        self.mode_seg = ctk.CTkSegmentedButton(
            top,
            values=["Automatic", "Manual"],
            variable=self.mode_var,
            font=ctk.CTkFont(size=14),
            width=260,
            command=self._on_mode_change,
        )
        self.mode_seg.pack(side="left", padx=8, pady=8)

        self.conn_label = ctk.CTkLabel(
            top,
            text="● READY",
            text_color="#00E5FF",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.conn_label.pack(side="right", padx=(0, 20), pady=4)

        # Center area
        center = ctk.CTkFrame(self, fg_color="transparent")
        center.pack(fill="both", expand=True, padx=pad, pady=(0, 4))

        # Main status
        self.status_var = ctk.StringVar(value="Idle")
        self.status_label = ctk.CTkLabel(
            center,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=FONT_STATUS, weight="bold"),
        )
        self.status_label.pack(pady=(8, 2))

        # Team name centered (S²ICD)
        # Use Unicode superscript 2 (\u00B2)
        team_text = "S\u00B2ICD"
        self.team_label = ctk.CTkLabel(
            center,
            text=team_text,
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=TEAM_NEON,
        )
        self.team_label.pack(pady=(2, 6))

        # Phase label under team name
        self.phase_var = ctk.StringVar(value="Phase: Idle")
        self.phase_label = ctk.CTkLabel(center, textvariable=self.phase_var, font=ctk.CTkFont(size=18))
        self.phase_label.pack(pady=(0, 6))

        eta_block = ctk.CTkFrame(center, fg_color="transparent")
        eta_block.pack(pady=(0, 8))
        ctk.CTkLabel(eta_block, text="Estimated time", font=ctk.CTkFont(size=14)).pack()
        self.eta_var = ctk.StringVar(value="--:--")
        self.eta_label = ctk.CTkLabel(
            eta_block,
            textvariable=self.eta_var,
            font=ctk.CTkFont(size=FONT_ETA, weight="bold"),
            text_color="#00FFCC",
        )
        self.eta_label.pack()

        prog_frame = ctk.CTkFrame(center, fg_color="transparent")
        prog_frame.pack(fill="x", padx=80, pady=(8, 10))
        self.progress = ctk.CTkProgressBar(prog_frame, width=540, height=18)
        self.progress.set(0.0)
        self.progress.pack(side="left", padx=(4, 10), pady=2)

        self.pct_label = ctk.CTkLabel(prog_frame, text="0%", width=40, font=ctk.CTkFont(size=14))
        self.pct_label.pack(side="left", pady=2)

        # Bottom buttons
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(pady=(0, 8))

        self.start_btn = ctk.CTkButton(
            bottom,
            text="START",
            width=CIRCLE_BTN,
            height=CIRCLE_BTN,
            corner_radius=CIRCLE_BTN // 2,
            fg_color=NEON_GREEN,
            hover_color="#00e883",
            font=ctk.CTkFont(size=20, weight="bold"),
            command=self.start_pressed,
        )
        self.start_btn.pack(side="left", padx=(40, 12), pady=4)

        self.stop_btn = ctk.CTkButton(
            bottom,
            text="STOP",
            width=CIRCLE_BTN,
            height=CIRCLE_BTN,
            corner_radius=CIRCLE_BTN // 2,
            fg_color=NEON_YELLOW,
            hover_color="#f4b000",
            font=ctk.CTkFont(size=20, weight="bold"),
            command=self.stop_pressed,
            state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(12, 12), pady=4)

        # OUTPUT button (cycles between: idle->steady_high->cycle->steady_high->...)
        self.toggle_btn = ctk.CTkButton(
            bottom,
            text="OUTPUT: OFF",
            width=CIRCLE_BTN,
            height=CIRCLE_BTN,
            corner_radius=CIRCLE_BTN // 2,
            fg_color=TOGGLE_BLUE,
            hover_color="#3333CC",
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self._output_pressed,
        )
        self.toggle_btn.pack(side="left", padx=(12, 40), pady=4)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=pad, pady=(0, 6))
        self.small_info = ctk.CTkLabel(
            footer,
            text="OUTPUT behavior: default LOW. 1st press -> steady HIGH. 2nd press -> start cycle (LOW then HIGH3/LOW5). Next press -> steady HIGH.",
            font=ctk.CTkFont(size=11),
        )
        self.small_info.pack()

    # ---------------- Mode / ETA ----------------
    def _on_mode_change(self, value):
        if self.running or self.paused:
            self.mode_var.set(self.current_mode)
            return
        self.current_mode = value
        self.manual_state = "idle"
        self.current_stage = None
        self.stage_total = 0.0
        self.stage_remaining = 0.0
        self.status_var.set("Idle")
        self.phase_var.set("Phase: Idle")
        self.progress.set(0.0)
        self.pct_label.configure(text="0%")
        self.start_btn.configure(text="START", state="normal")
        self.stop_btn.configure(state="disabled")
        if value == "Automatic":
            self.eta_var.set("00:30")
        else:
            self.eta_var.set("--:--")

    # ---------------- KILL ----------------
    def _do_kill(self):
        if self.killed:
            return
        self.killed = True
        # stop any timer
        self.running = False
        self.paused = False
        self.pause_flag = False

        # Stop cycle and force LOW
        self._stop_gpio_cycle()
        try:
            self.led.off()
        except Exception:
            pass

        self.status_var.set("EMERGENCY STOP")
        self.phase_var.set("Phase: Aborted")
        self.conn_label.configure(text="● KILLED", text_color=DANGER_RED)
        self.progress.set(0.0)
        self.pct_label.configure(text="0%")
        self.eta_var.set("--:--")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        self.kill_btn.configure(state="disabled")
        self.toggle_btn.configure(state="disabled")
        print("[KILL] Emergency stop triggered; GPIO forced LOW")

    # ---------------- OUTPUT button behavior ----------------
    def _output_pressed(self):
        """
        Cycle the OUTPUT state:
         - idle -> steady_high (LED ON steady)
         - steady_high -> cycle (start cycle; cycle begins with LOW)
         - cycle -> steady_high (stop cycle, LED ON steady)
        """
        if self.killed:
            return

        with self._gpio_mode_lock:
            mode = self._gpio_mode

            if mode == 'idle':
                # go to steady_high
                self._set_steady_high()
                self._gpio_mode = 'steady_high'
                self.toggle_btn.configure(text="OUTPUT: HIGH", fg_color=TOGGLE_GREEN)
                print("[OUTPUT] idle -> steady HIGH")
                return

            if mode == 'steady_high':
                # switch to cycle (start cycle thread); cycle should start with LOW
                self._start_gpio_cycle(start_with_low=True)
                self._gpio_mode = 'cycle'
                self.toggle_btn.configure(text="OUTPUT: CYCLE", fg_color="#FFAA00")
                print("[OUTPUT] steady HIGH -> CYCLE (starting with LOW)")
                return

            if mode == 'cycle':
                # stop cycle and set steady HIGH
                self._stop_gpio_cycle()
                self._set_steady_high()
                self._gpio_mode = 'steady_high'
                self.toggle_btn.configure(text="OUTPUT: HIGH", fg_color=TOGGLE_GREEN)
                print("[OUTPUT] cycle -> steady HIGH (cycle stopped)")
                return

    def _set_steady_high(self):
        # Stop any cycle thread then set LED ON
        self._stop_gpio_cycle()
        try:
            self.led.on()
        except Exception:
            pass

    # ---------------- GPIO cycle thread ----------------
    def _start_gpio_cycle(self, start_with_low: bool = True):
        # Safety: if thread already running, make sure it's the one we control
        if self._gpio_cycle_thread and self._gpio_cycle_thread.is_alive():
            print("[GPIO] cycle thread already running")
            return
        self._gpio_cycle_stop_evt.clear()

        def _cycle_worker():
            try:
                # If requested, begin with a LOW period (immediately)
                if start_with_low:
                    try:
                        self.led.off()
                    except Exception:
                        pass
                    print("[GPIO] cycle: initial LOW period (starting cycle)")
                    total = GPIO_LOW_SEC  # start with LOW sec before the normal pattern
                    step = 0.1
                    for _ in range(int(total / step)):
                        if self._gpio_cycle_stop_evt.is_set() or self.killed:
                            break
                        # check if mode changed externally
                        with self._gpio_mode_lock:
                            if self._gpio_mode != 'cycle':
                                break
                        time.sleep(step)
                    if self._gpio_cycle_stop_evt.is_set() or self.killed:
                        try:
                            self.led.off()
                        except Exception:
                            pass
                        print("[GPIO] cycle aborted during initial LOW")
                        return

                # Now normal repeating pattern: HIGH then LOW
                while not self._gpio_cycle_stop_evt.is_set() and not self.killed:
                    # ensure still in 'cycle' mode
                    with self._gpio_mode_lock:
                        if self._gpio_mode != 'cycle':
                            break

                    # HIGH
                    try:
                        self.led.on()
                    except Exception:
                        pass
                    print("[GPIO] CYCLE: HIGH for {:.1f}s".format(GPIO_HIGH_SEC))
                    for _ in range(int(GPIO_HIGH_SEC / 0.1)):
                        if self._gpio_cycle_stop_evt.is_set() or self.killed:
                            break
                        with self._gpio_mode_lock:
                            if self._gpio_mode != 'cycle':
                                break
                        time.sleep(0.1)
                    if self._gpio_cycle_stop_evt.is_set() or self.killed:
                        break

                    # LOW
                    try:
                        self.led.off()
                    except Exception:
                        pass
                    print("[GPIO] CYCLE: LOW for {:.1f}s".format(GPIO_LOW_SEC))
                    for _ in range(int(GPIO_LOW_SEC / 0.1)):
                        if self._gpio_cycle_stop_evt.is_set() or self.killed:
                            break
                        with self._gpio_mode_lock:
                            if self._gpio_mode != 'cycle':
                                break
                        time.sleep(0.1)

                # ensure LOW before exit
                try:
                    self.led.off()
                except Exception:
                    pass
                print("[GPIO] cycle thread exiting and GPIO forced LOW")
            except Exception:
                print("[GPIO] cycle thread exception:\n", traceback.format_exc())
                try:
                    self.led.off()
                except Exception:
                    pass

        t = threading.Thread(target=_cycle_worker, daemon=True)
        self._gpio_cycle_thread = t
        t.start()

    def _stop_gpio_cycle(self):
        # tell thread to stop and ensure LED OFF
        self._gpio_cycle_stop_evt.set()
        try:
            self.led.off()
        except Exception:
            pass

    # ---------------- START/STOP (timed stages) ----------------
    def start_pressed(self):
        if self.killed:
            print("[START] Ignored — device KILLED")
            return
        if self.paused and self.current_stage is not None:
            self.paused = False
            self.pause_flag = False
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self._start_worker_for_current_stage()
            return
        mode = self.mode_var.get()
        if mode == "Automatic":
            if self.running:
                return
            self._start_auto_new()
        else:
            self._start_manual_flow()

    def stop_pressed(self):
        if self.killed:
            return
        if self.running and self.current_stage is not None:
            self.pause_flag = True
            self.stop_btn.configure(state="disabled")
            self.start_btn.configure(state="disabled")
            self.status_var.set("Pausing...")
            print("[STOP] Pause requested")
            return

    # ---------------- Timer flows (unchanged) ----------------
    def _start_auto_new(self):
        self.current_stage = "auto"
        self.stage_total = AUTO_TOTAL
        self.stage_remaining = AUTO_TOTAL
        self.status_var.set("Starting (Automatic)...")
        self.phase_var.set("Phase: Initializing")
        self.progress.set(0.0)
        self.pct_label.configure(text="0%")
        self.eta_var.set(self._format_seconds(self.stage_remaining))
        self.start_btn.configure(state="disabled", text="START")
        self.stop_btn.configure(state="normal")
        self._start_worker_for_current_stage()

    def _start_manual_flow(self):
        if self.manual_state == "idle":
            self.current_stage = "foam"
            self.stage_total = MANUAL_FOAM
            self.stage_remaining = MANUAL_FOAM
            self.status_var.set("Manual: Foam dispersion running")
            self.phase_var.set("Phase: Foam dispersion")
            self.progress.set(0.0)
            self.pct_label.configure(text="0%")
            self.eta_var.set(self._format_seconds(self.stage_remaining))
            self.start_btn.configure(state="disabled", text="START")
            self.stop_btn.configure(state="normal")
            self._start_worker_for_current_stage()
            return
        if self.manual_state == "foam_done":
            self.current_stage = "inflate"
            self.stage_total = MANUAL_INFLATE
            self.stage_remaining = MANUAL_INFLATE
            self.status_var.set("Manual: Balloon inflation running")
            self.phase_var.set("Phase: Balloon inflation")
            self.progress.set(0.0)
            self.pct_label.configure(text="0%")
            self.eta_var.set(self._format_seconds(self.stage_remaining))
            self.start_btn.configure(state="disabled", text="Start balloon inflation")
            self.stop_btn.configure(state="normal")
            self._start_worker_for_current_stage()
            return

    def _start_worker_for_current_stage(self):
        self.running = True
        self.paused = False
        self.stop_btn.configure(state="normal")
        t = threading.Thread(target=self._stage_worker, daemon=True)
        self.worker = t
        t.start()

    def _stage_worker(self):
        last_time = time.time()
        while self.stage_remaining > 0:
            if self.killed:
                self.running = False
                return
            if self.pause_flag:
                self.running = False
                self.paused = True
                self.pause_flag = False
                self.after(0, self._on_paused)
                return
            now = time.time()
            dt = now - last_time
            last_time = now
            self.stage_remaining = max(0.0, self.stage_remaining - dt)
            frac = (self.stage_total - self.stage_remaining) / self.stage_total if self.stage_total > 0 else 1.0
            if self.current_stage == "auto":
                if frac < 1/3:
                    phase = "Phase: Foam dispersion"
                elif frac < 2/3:
                    phase = "Phase: Balloon inflation"
                else:
                    phase = "Phase: Stabilizing"
                status = f"Running (Automatic)... {int(frac*100)}%"
            else:
                if self.current_stage == "foam":
                    phase = "Phase: Foam dispersion"
                elif self.current_stage == "inflate":
                    phase = "Phase: Balloon inflation"
                elif self.current_stage == "stabilize":
                    phase = "Phase: Stabilizing"
                else:
                    phase = "Phase: Unknown"
                status = f"Running (Manual)... {int(frac*100)}%"
            eta_str = self._format_seconds(self.stage_remaining)
            self.after(0, self.progress.set, frac)
            self.after(0, self.pct_label.configure, {"text": f"{int(frac*100)}%"} )
            self.after(0, self.eta_var.set, eta_str)
            self.after(0, self.status_var.set, status)
            self.after(0, self.phase_var.set, phase)
            time.sleep(0.1)
        self.running = False
        self.paused = False
        self.after(0, self._on_stage_complete)

    def _on_paused(self):
        if self.killed:
            return
        self.status_var.set("Paused")
        self.start_btn.configure(state="normal", text="RESUME")
        self.stop_btn.configure(state="disabled")
        print("[PAUSE] Stage paused:", self.current_stage)

    def _on_stage_complete(self):
        if self.killed:
            return
        stage = self.current_stage
        print("[STAGE COMPLETE]", stage)
        self.progress.set(1.0)
        self.pct_label.configure(text="100%")
        self.eta_var.set("00:00")
        if stage == "auto":
            self.status_var.set("Completed (Automatic)")
            self.phase_var.set("Phase: Done")
            self.current_stage = None
            self.start_btn.configure(state="normal", text="START")
            self.stop_btn.configure(state="disabled")
            return
        if stage == "foam":
            self.manual_state = "foam_done"
            self.current_stage = None
            self.status_var.set("Foam dispersion completed")
            self.phase_var.set("Phase: Foam done")
            self.start_btn.configure(state="normal", text="Start balloon inflation")
            self.stop_btn.configure(state="disabled")
            self.eta_var.set(self._format_seconds(MANUAL_INFLATE))
            return
        if stage == "inflate":
            self.manual_state = "inflate_done"
            self.current_stage = "stabilize"
            self.stage_total = MANUAL_STABILIZE
            self.stage_remaining = MANUAL_STABILIZE
            self.status_var.set("Stabilizing...")
            self.phase_var.set("Phase: Stabilizing")
            self.progress.set(0.0)
            self.pct_label.configure(text="0%")
            self.eta_var.set(self._format_seconds(MANUAL_STABILIZE))
            self.start_btn.configure(state="disabled", text="START")
            self.stop_btn.configure(state="normal")
            self._start_worker_for_current_stage()
            return
        if stage == "stabilize":
            self.manual_state = "manual_done"
            self.current_stage = None
            self.status_var.set("Completed (Manual)")
            self.phase_var.set("Phase: Done")
            self.start_btn.configure(state="normal", text="START")
            self.stop_btn.configure(state="disabled")
            return

    # ---------------- Helpers / cleanup ----------------
    def _format_seconds(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        m = seconds // 60
        s = seconds % 60
        return f"{m:02d}:{s:02d}"

    def _on_close(self):
        # ensure cycle stops and LED is LOW
        try:
            self._stop_gpio_cycle()
            self.led.off()
        except Exception:
            pass
        self.destroy()
        try:
            sys.exit(0)
        except Exception:
            pass

if __name__ == "__main__":
    app = SimpleDeviceUI()
    app.mainloop()
s
