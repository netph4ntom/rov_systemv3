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


import cv2
import numpy as np
import time
import logging

logger = logging.getLogger(__name__)

class YOLODetector(Detector):
    """
    Detector menggunakan model YOLO format ONNX via ONNXRuntime.
    Lebih ringan dari PyTorch namun 100% kompatibel dengan semua versi YOLO (termasuk layer Attention/Split).
    """
    def __init__(self):
        self.session = None
        self.input_name = None
        self.output_name = None
        self.is_loaded = False
        
        try:
            import onnxruntime as ort
            self._ort = ort
        except ImportError:
            logger.error("[ONNXDetector] onnxruntime package not found. Please run: pip install onnxruntime")
            self._ort = None

        from config import VISION_CONFIDENCE_THRESHOLD
        # Default input size untuk YOLOv8 (640x640)
        self.input_width = 640
        self.input_height = 640
        self.conf_threshold = VISION_CONFIDENCE_THRESHOLD
        self.iou_threshold = 0.4
        
        # Pemetaan nama class (sesuaikan dengan urutan saat training YOLO)
        self.classes = {
            0: "payload",
            1: "dock",
            2: "rov",
            3: "obstacle"
        } 

    def load_model(self, model_path: str) -> bool:
        if self._ort is None:
            return False
            
        if not model_path.endswith(".onnx"):
            logger.warning("[ONNXDetector] Model bukan berakhiran .onnx. Harap pastikan model sudah di-export ke ONNX.")
            
        try:
            logger.info(f"[ONNXDetector] Loading ONNX model from {model_path} via ONNXRuntime...")
            # Gunakan CPUExecutionProvider untuk kompatibilitas maksimal di Raspberry Pi
            self.session = self._ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            self.input_name = self.session.get_inputs()[0].name
            self.output_name = self.session.get_outputs()[0].name
            
            self.is_loaded = True
            logger.info("[ONNXDetector] Model ONNX loaded successfully.")
            return True
        except Exception as e:
            logger.error(f"[ONNXDetector] Failed to load model {model_path}: {e}")
            self.is_loaded = False
            return False

    def detect(self, frame) -> List[DetectionResult]:
        if not self.is_loaded or self.session is None or frame is None:
            return []

        try:
            original_image = frame
            [height, width, _] = original_image.shape
            
            # Prepare image for DNN (YOLOv8 expects RGB, 1/255.0 normalization, 640x640)
            blob = cv2.dnn.blobFromImage(original_image, 1/255.0, (self.input_width, self.input_height), swapRB=True, crop=False)
            
            # Run forward pass via ONNXRuntime
            outputs = self.session.run([self.output_name], {self.input_name: blob})[0]
            
            # YOLOv8 ONNX output shape is (1, num_classes + 4, 8400)
            # Transpose to (8400, num_classes + 4) for easier processing
            outputs = np.array([cv2.transpose(outputs[0])])
            rows = outputs.shape[1]
            
            boxes = []
            scores = []
            class_ids = []
            
            # Calculate scaling factors
            x_factor = width / self.input_width
            y_factor = height / self.input_height
            
            # Iterate through detections
            for i in range(rows):
                classes_scores = outputs[0][i][4:]
                
                # Gunakan numpy argmax (lebih robust dari cv2.minMaxLoc)
                maxClassIndex = np.argmax(classes_scores)
                maxScore = classes_scores[maxClassIndex]
                
                if maxScore >= self.conf_threshold:
                    box = [
                        float(outputs[0][i][0] - (0.5 * outputs[0][i][2])), # x_min
                        float(outputs[0][i][1] - (0.5 * outputs[0][i][3])), # y_min
                        float(outputs[0][i][2]), # w
                        float(outputs[0][i][3])  # h
                    ]
                    boxes.append(box)
                    scores.append(float(maxScore))
                    class_ids.append(int(maxClassIndex))

            # Apply Non-Maximum Suppression (NMS)
            # Hasil NMSBoxes bisa berupa list, tuple, atau numpy array tergantung versi OpenCV
            result_boxes = cv2.dnn.NMSBoxes(boxes, scores, self.conf_threshold, self.iou_threshold)
            
            detections = []
            now = time.time()
            
            if len(result_boxes) > 0:
                indices = np.array(result_boxes).flatten()
                for index in indices:
                    box = boxes[index]
                    
                    # Scale boxes back to original image size
                    x1 = int(box[0] * x_factor)
                    y1 = int(box[1] * y_factor)
                    w  = int(box[2] * x_factor)
                    h  = int(box[3] * y_factor)
                    x2 = x1 + w
                    y2 = y1 + h
                    
                    cls_id = class_ids[index]
                    conf = float(scores[index])
                    cls_name = self.classes.get(cls_id, f"class_{cls_id}")
                
                center_x = int(x1 + w/2)
                center_y = int(y1 + h/2)
                
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
            logger.error(f"[ONNXDetector] Error during detection: {e}")
            return []
