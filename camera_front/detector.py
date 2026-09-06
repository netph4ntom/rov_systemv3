import time
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class DetectionResult:
    def __init__(self, class_id: int, class_name: str, confidence: float, 
                 x1: int, y1: int, x2: int, y2: int, 
                 center_x: int, center_y: int, timestamp: float):
        self.class_id = class_id
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        self.center = {"x": center_x, "y": center_y}
        self.timestamp = timestamp

    def to_dict(self) -> Dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "bbox": self.bbox,
            "center": self.center,
            "timestamp": self.timestamp
        }

class Detector:
    def load_model(self, model_path: str) -> bool:
        raise NotImplementedError

    def detect(self, frame) -> List[DetectionResult]:
        raise NotImplementedError


class YOLODetector(Detector):
    def __init__(self):
        self.model = None
        self.is_loaded = False
        
        try:
            from ultralytics import YOLO
            self._YOLO_cls = YOLO
        except ImportError:
            logger.error("[YOLODetector] ultralytics package not found. Please install it.")
            self._YOLO_cls = None

    def load_model(self, model_path: str) -> bool:
        if self._YOLO_cls is None:
            return False
            
        try:
            logger.info(f"[YOLODetector] Loading model from {model_path}...")
            self.model = self._YOLO_cls(model_path)
            self.is_loaded = True
            logger.info("[YOLODetector] Model loaded successfully.")
            return True
        except Exception as e:
            logger.error(f"[YOLODetector] Failed to load model {model_path}: {e}")
            self.is_loaded = False
            return False

    def detect(self, frame) -> List[DetectionResult]:
        if not self.is_loaded or self.model is None or frame is None:
            return []

        try:
            results = self.model(frame, verbose=False)
            detections = []
            now = time.time()
            
            for result in results:
                boxes = result.boxes
                for box in boxes:
                    # extract box coordinates
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    
                    # extract confidence
                    conf = float(box.conf[0])
                    
                    # extract class info
                    cls_id = int(box.cls[0])
                    cls_name = self.model.names[cls_id]
                    
                    center_x = int((x1 + x2) / 2)
                    center_y = int((y1 + y2) / 2)
                    
                    det = DetectionResult(
                        class_id=cls_id,
                        class_name=cls_name,
                        confidence=conf,
                        x1=x1, y1=y1, x2=x2, y2=y2,
                        center_x=center_x, center_y=center_y,
                        timestamp=now
                    )
                    detections.append(det)
                    
            return detections
        except Exception as e:
            logger.error(f"[YOLODetector] Error during detection: {e}")
            return []
