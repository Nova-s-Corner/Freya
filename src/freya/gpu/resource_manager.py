import json
import time
import urllib.error
import urllib.request
import os
import logging
from pathlib import Path


OLLAMA_URL = "http://127.0.0.1:11434"
COMFYUI_URL = "http://127.0.0.1:8188"

# VRAM threshold configuration (in GB)
VRAM_THRESHOLD_GB = 2.0

# Log file path for VRAM alerts
ALERT_LOG_PATH = Path("~/.freya/vram_alerts.log")


def setup_logging():
    """Set up logging for VRAM alerts."""
    log_path = ALERT_LOG_PATH.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Clear existing log file
    if log_path.exists():
        log_path.unlink()
    
    logger = logging.getLogger("vram_alerts")
    logger.setLevel(logging.WARNING)
    
    handler = logging.FileHandler(log_path, mode='w')
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | VRAM: %(vram_gb).2f GB | Message: %(message)s'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


class GPUResourceManager:
    """
    Freya GPU Resource Manager v1.

    Coordinates GPU ownership between Ollama and ComfyUI.

    Stage 1 focuses on:
        - detecting loaded Ollama models
        - unloading Ollama models
        - detecting ComfyUI
        - requesting ComfyUI model unload/free-memory
        - reporting resource state

    No automatic switching is performed yet.
    """

    def __init__(
        self,
        ollama_url=OLLAMA_URL,
        comfyui_url=COMFYUI_URL,
    ):
        self.ollama_url = ollama_url.rstrip("/")
        self.comfyui_url = comfyui_url.rstrip("/")
        self.logger = setup_logging()

    def _request_json(
        self,
        url,
        method="GET",
        payload=None,
        timeout=30,
    ):
        data = None
        headers = {}

        if payload is not None:
            data = json.dumps(
                payload
            ).encode("utf-8")

            headers[
                "Content-Type"
            ] = "application/json"

        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            body = response.read()

            if not body:
                return {}

            return json.loads(
                body.decode("utf-8")
            )

    def ollama_available(self):
        try:
            self._request_json(
                f"{self.ollama_url}/api/ps"
            )
            return True

        except (
            urllib.error.URLError,
            TimeoutError,
        ):
            return False

    def comfyui_available(self):
        try:
            self._request_json(
                f"{self.comfyui_url}/system_stats"
            )
            return True

        except (
            urllib.error.URLError,
            TimeoutError,
        ):
            return False

    def get_ollama_models(self):
        try:
            response = self._request_json(
                f"{self.ollama_url}/api/ps"
            )

        except urllib.error.URLError:
            return []

        return response.get(
            "models",
            [],
        )

    def unload_ollama_model(
        self,
        model_name,
    ):
        print(
            "GPU Manager: unloading Ollama model "
            f"{model_name}..."
        )

        self._request_json(
            f"{self.ollama_url}/api/generate",
            method="POST",
            payload={
                "model": model_name,
                "keep_alive": 0,
            },
            timeout=60,
        )

    def unload_all_ollama_models(self):
        models = self.get_ollama_models()

        if not models:
            print(
                "GPU Manager: no Ollama models "
                "are currently loaded."
            )
            return

        for model in models:
            model_name = (
                model.get("name")
                or model.get("model")
            )

            if not model_name:
                continue

            self.unload_ollama_model(
                model_name
            )

        self.wait_for_ollama_unload()

    def wait_for_ollama_unload(
        self,
        timeout=30,
    ):
        start_time = time.monotonic()

        while True:
            models = self.get_ollama_models()

            if not models:
                print(
                    "GPU Manager: Ollama GPU "
                    "models released."
                )
                return True

            if (
                time.monotonic() - start_time
                > timeout
            ):
                raise TimeoutError(
                    "Timed out waiting for "
                    "Ollama models to unload."
                )

            time.sleep(0.25)

    def free_comfyui_memory(self):
        if not self.comfyui_available():
            print(
                "GPU Manager: ComfyUI is not running."
            )
            return False

        print(
            "GPU Manager: requesting ComfyUI "
            "model unload..."
        )

        self._request_json(
            f"{self.comfyui_url}/free",
            method="POST",
            payload={
                "unload_models": True,
                "free_memory": True,
            },
        )

        print(
            "GPU Manager: ComfyUI model "
            "unload requested."
        )

        return True

    def _log_alert(self, message):
        """Write an alert to the log file."""
        self.logger.warning(message)

    def get_total_vram_used(self):
        """
        Calculate total VRAM usage from both Ollama and ComfyUI.
        
        Returns:
            float: Total VRAM used in GB, or 0 if neither is available
        """
        vram = 0.0
        
        # Check Ollama models
        if self.ollama_available():
            models = self.get_ollama_models()
            for model in models:
                size_vram = model.get("size_vram", 0)
                vram += size_vram / (1024 ** 3)  # Convert to GB
        
        # Check ComfyUI memory usage
        if self.comfyui_available():
            try:
                stats = self._request_json(
                    f"{self.comfyui_url}/system_stats"
                )
                # ComfyUI provides total VRAM used in GB
                vram_used = stats.get("vram", {}).get("used", 0)
                vram += vram_used
            except Exception:
                pass
        
        return vram
    
    def _log_vram_status(self, vram_gb):
        """Log current VRAM status for monitoring."""
        threshold_percent = (vram_gb / VRAM_THRESHOLD_GB) * 100 if VRAM_THRESHOLD_GB > 0 else 0
        self.logger.info(f"VRAM Status: {vram_gb:.2f} GB used ({threshold_percent:.1f}% of threshold)")
    
    def monitor_vram(self, interval=5):
        """
        Continuously monitor VRAM usage and log alerts when threshold is exceeded.
        
        Args:
            interval (int): Check interval in seconds (default: 5)
        """
        self._log_vram_status(0.0)
        
        while True:
            vram_gb = self.get_total_vram_used()
            self._log_vram_status(vram_gb)
            
            # Check if threshold exceeded
            if vram_gb >= VRAM_THRESHOLD_GB:
                self._log_alert(
                    f"⚠️ VRAM THRESHOLD EXCEEDED! "
                    f"Current usage: {vram_gb:.2f} GB (threshold: {VRAM_THRESHOLD_GB} GB)"
                )
            
            time.sleep(interval)
    
    def start_vram_monitoring(self, interval=5):
        """
        Start VRAM monitoring in a background thread.
        
        Args:
            interval (int): Check interval in seconds (default: 5)
        """
        import threading
        thread = threading.Thread(
            target=self.monitor_vram,
            args=(interval,),
            daemon=True
        )
        thread.start()
        return thread
    
    def print_status(self):
        print()
        print("=== Freya GPU Resource Manager ===")

        if self.ollama_available():
            print("Ollama server: ONLINE")

            models = self.get_ollama_models()

            if models:
                print("Ollama loaded models:")

                for model in models:
                    name = (
                        model.get("name")
                        or model.get("model")
                        or "Unknown"
                    )

                    size_vram = model.get(
                        "size_vram",
                        0,
                    )

                    vram_gb = (
                        size_vram
                        / 1024
                        / 1024
                        / 1024
                    )

                    print(
                        f"  - {name} "
                        f"({vram_gb:.2f} GB VRAM)"
                    )

            else:
                print(
                    "Ollama loaded models: none"
                )

        else:
            print("Ollama server: OFFLINE")

        if self.comfyui_available():
            try:
                stats = self._request_json(
                    f"{self.comfyui_url}/system_stats"
                )
                vram_used = stats.get("vram", {}).get("used", 0)
                print(f"ComfyUI VRAM used: {vram_used:.2f} GB")
            except Exception as e:
                print(f"ComfyUI: Could not fetch stats ({e})")
        else:
            print("ComfyUI server: OFFLINE")
    
    def stop_vram_monitoring(self):
        """Stop any running VRAM monitoring thread."""
        pass  # Would need to track the thread in a real implementation

