import os
import sys
import threading
from typing import Optional, Dict, Any, List
from freya.core.assistant import Assistant
from freya.gateway.server import GatewayServer
from freya.spatial_avatar.renderer import SpatialAvatarRenderer
from freya.speech.synthesizer import SpeechSynthesizer
from freya.image.generator import ImageGenerator
from freya.gallery import Gallery
from freya.events import Events

# Global state with proper initialization
SHUTTING_DOWN = threading.Event()
GALLERY: Optional[Gallery] = None
VOICE_FILE_COMMAND = "voice_file:"
SPEECH_BUFFER = ""

def init_globals():
    """Initialize global objects safely."""
    global GALLERY
    if GALLERY is None:
        try:
            from freya.gallery import Gallery as GalleryClass
            GALLERY = GalleryClass()
            EVENTS.publish("gallery_initialized")
        except Exception as e:
            print(f"Warning: Failed to initialize gallery: {e}")
            GALLERY = None

def keep_generated_image(prompt: str, result: Dict[str, Any]) -> None:
    """Keep a generated image in the gallery with proper null checks."""
    global GALLERY
    
    if GALLERY is None:
        EVENTS.publish("image_failed", {"prompt": prompt, "reason": "gallery_not_initialized"})
        return
    
    if not result or "image_urls" not in result or not result["image_urls"]:
        EVENTS.publish("image_failed", {"prompt": prompt, "reason": "no_image_urls"})
        return
    
    for url in result["image_urls"]:
        try:
            GALLERY.add_image(url, prompt)
            EVENTS.publish("image_added", {"prompt": prompt, "url": url})
        except Exception as e:
            print(f"Error adding image to gallery: {e}")
            EVENTS.publish("image_failed", {"prompt": prompt, "reason": str(e)})

def get_voice_file_path(command: Any) -> Optional[str]:
    """Extract voice file path from command with proper validation."""
    if not isinstance(command, str):
        return None
    
    try:
        # Validate command contains the prefix
        if VOICE_FILE_COMMAND not in command:
            return None
        
        # Safely extract the path
        path = command[len(VOICE_FILE_COMMAND):].strip()
        return path.strip('"')
    except Exception as e:
        print(f"Error extracting voice file path: {e}")
        return None

def shutdown_safely() -> None:
    """Safely shut down all components with proper synchronization."""
    global SHUTTING_DOWN
    
    if SHUTTING_DOWN.is_set():
        return
    
    SHUTTING_DOWN.set()
    
    try:
        # Close speech buffer
        if SPEECH_BUFFER:
            SPEECH_BUFFER = ""
    except Exception as e:
        print(f"Error during shutdown cleanup: {e}")

def main():
    """Main entry point with proper initialization and error handling."""
    global SHUTTING_DOWN, GALLERY
    
    try:
        # Initialize all components
        init_globals()
        
        assistant = Assistant()
        gateway = GatewayServer()
        renderer = SpatialAvatarRenderer()
        synthesizer = SpeechSynthesizer()
        image_gen = ImageGenerator()
        
        # Start components
        threading.Thread(target=assistant.run, daemon=True).start()
        threading.Thread(target=gateway.start, daemon=True).start()
        threading.Thread(target=renderer.run, daemon=True).start()
        threading.Thread(target=synthesizer.run, daemon=True).start()
        threading.Thread(target=image_gen.run, daemon=True).start()
        
        print("Freya startup complete. Server running on port 8080.")
        
    except Exception as e:
        print(f"Fatal error during startup: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Keep running until shutdown signal
    while not SHUTTING_DOWN.is_set():
        try:
            import time
            time.sleep(0.1)
        except Exception as e:
            print(f"Error in main loop: {e}")
    
    # Graceful shutdown
    shutdown_safely()
    print("Freya shutting down...")
    sys.exit(0)

if __name__ == "__main__":
    main()