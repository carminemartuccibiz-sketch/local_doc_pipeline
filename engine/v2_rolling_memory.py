from __future__ import annotations
import logging
from typing import Callable, Any
from engine.v2_map_manager import V2MapManager

logger = logging.getLogger(__name__)

# Tipo per la chiamata LLM di estrazione
FactExtractorLLM = Callable[..., dict[str, Any]]

class V2RollingMemory:
    def __init__(
        self,
        memory_dir: Any, # Passato dal layout
        map_manager: V2MapManager,
        extractor: FactExtractorLLM,
        log_fn: Callable[[str], None] = None,
    ):
        self.map_manager = map_manager
        self.extractor = extractor
        self.log_fn = log_fn or (lambda m: logger.info(m))

    def process_all_chunks(self, layout, stop_event=None) -> int:
        chunks = self.map_manager.list_chunks()
        rolling_state = self.map_manager.get_rolling_memory().get("compressed_context", "")
        processed_count = 0
        
        for chunk in chunks:
            if stop_event and stop_event.is_set():
                break
                
            # Salta se già fatto
            if chunk.get("knowledge_state", {}).get("rolling_context_merged"):
                continue
            
            chunk_path = layout.root / chunk["path"]
            if not chunk_path.is_file():
                continue
                
            chunk_text = chunk_path.read_text(encoding="utf-8", errors="ignore")
            self.log_fn(f"[ROLLING] Compressione semantica per {chunk['id']}...")
            
            try:
                # L'LLM riceve il chunk e la memoria precedente
                extraction = self.extractor(
                    text=chunk_text,
                    history=rolling_state
                )
                
                # extraction deve ritornare {"structured_facts": {...}, "new_compressed_context": "..."}
                facts = extraction.get("structured_facts", {})
                new_context = extraction.get("new_compressed_context", rolling_state)
                
                self.map_manager.merge_rolling_structured(facts)
                self.map_manager.set_chunk_knowledge_state(chunk["id"], rolling_context_merged=True)
                self.map_manager.set_compressed_context(new_context)
                
                rolling_state = new_context
                processed_count += 1
                
            except Exception as e:
                self.log_fn(f"[ROLLING ERROR] Fallimento su {chunk['id']}: {e}")
                
        return processed_count