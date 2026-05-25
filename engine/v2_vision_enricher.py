from __future__ import annotations
import logging
from typing import Callable, Any
from engine.project_memory import V2StagingLayout
from engine.v2_map_manager import V2MapManager

logger = logging.getLogger(__name__)

# Definizione del tipo per l'iniezione di dipendenza
VisionLLMCaller = Callable[..., Any]

class V2VisionEnricher:
    def __init__(
        self,
        layout: V2StagingLayout,
        map_manager: V2MapManager,
        vision_caller: VisionLLMCaller,
        log_fn: Callable[[str], None] = None,
    ):
        self.layout = layout
        self.map_manager = map_manager
        self.vision_caller = vision_caller
        self.log_fn = log_fn or (lambda m: logger.info(m))

    def enrich_all_pending(self, stop_event=None) -> list[dict[str, Any]]:
        results = []
        for img in self.map_manager.list_images():
            if stop_event and stop_event.is_set():
                break
                
            if img.get("vision_processed"):
                continue

            # Recupera contesto testo
            linked_chunks = [self.map_manager.get_chunk(c) for c in img.get("linked_chunks", [])]
            context_text = ""
            for c in linked_chunks:
                if c and c.get("path"):
                    chunk_path = self.layout.root / c["path"]
                    if chunk_path.is_file():
                        context_text += chunk_path.read_text(encoding="utf-8", errors="ignore") + "\n"

            img_path = self.layout.root / img["path"]
            
            self.log_fn(f"[VISION] Analizzo immagine {img['id']} con LLM visivo...")
            try:
                # Chiamata LLM agnostica
                vision_insight = self.vision_caller(
                    image_path=img_path,
                    context=context_text,
                    prompt="Spiega in dettaglio questa immagine nel contesto del documento fornito."
                )
                
                # Aggiorna JSON
                self.map_manager.update_image(img["id"], {"vision_processed": True})
                self.map_manager.merge_rolling_structured({"vision_insights": [{"image_id": img["id"], "insight": vision_insight}]})
                results.append({"image_id": img["id"], "insight": vision_insight})
                
            except Exception as e:
                self.log_fn(f"[VISION ERROR] Fallimento su {img['id']}: {e}")
                
        return results