import argparse
import time
from typing import Dict, Tuple

import cv2
import numpy as np
from tflite_runtime.interpreter import Interpreter


def load_labels(path: str) -> Dict[int, str]:
    with open(path, "r", encoding="utf-8") as f:
        return {i: line.strip() for i, line in enumerate(f.readlines())}


def get_input_spec(interpreter: Interpreter) -> Tuple[int, int, np.dtype]:
    d = interpreter.get_input_details()[0]
    _, height, width, _ = d["shape"]
    return int(height), int(width), d["dtype"]


def preprocess_frame(bgr_square: np.ndarray, height: int, width: int, dtype: np.dtype) -> np.ndarray:
    rgb = cv2.cvtColor(bgr_square, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)

    if dtype == np.uint8:
        return resized.astype(np.uint8)

    # float32/float16 models: normalize to 0~1
    return (resized.astype(np.float32) / 255.0).astype(dtype, copy=False)


def classify(interpreter: Interpreter, input_image: np.ndarray, top_k: int = 1):
    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()[0]

    input_tensor = np.expand_dims(input_image, axis=0)
    interpreter.set_tensor(input_details["index"], input_tensor)
    interpreter.invoke()

    output = np.squeeze(interpreter.get_tensor(output_details["index"]))

    if output_details["dtype"] == np.uint8:
        scale, zero_point = output_details["quantization"]
        if scale > 0:
            output = scale * (output.astype(np.float32) - zero_point)
        else:
            output = output.astype(np.float32)
    else:
        output = output.astype(np.float32, copy=False)

    k = min(int(top_k), int(output.shape[0]))
    ordered = np.argpartition(-output, k - 1)[:k]
    ordered = ordered[np.argsort(-output[ordered])]
    return [(int(i), float(output[i])) for i in ordered]


def crop_center_square(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    size = min(h, w)
    x0 = (w - size) // 2
    y0 = (h - size) // 2
    return bgr[y0 : y0 + size, x0 : x0 + size]


def main() -> None:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--model", required=True, help="Path to .tflite file")
    parser.add_argument("--labels", required=True, help="Path to labels.txt")
    parser.add_argument("--camera", type=int, default=0, help="Camera index (usually 0)")
    parser.add_argument("--width", type=int, default=640, help="Capture width")
    parser.add_argument("--height", type=int, default=480, help="Capture height")
    parser.add_argument("--top_k", type=int, default=1, help="Top K predictions to compute")
    args = parser.parse_args()

    labels = load_labels(args.labels)
    interpreter = Interpreter(args.model)
    interpreter.allocate_tensors()

    in_h, in_w, in_dtype = get_input_spec(interpreter)

    cap = cv2.VideoCapture(args.camera)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    last_label = ""
    last_prob = 0.0
    t0 = time.time()
    fps = 0.0

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(0.05)
            continue

        square = crop_center_square(frame)
        input_img = preprocess_frame(square, in_h, in_w, in_dtype)

        start = time.time()
        results = classify(interpreter, input_img, top_k=args.top_k)
        infer_ms = (time.time() - start) * 1000.0

        if results:
            label_id, prob = results[0]
            last_label = labels.get(label_id, str(label_id))
            last_prob = prob

        dt = time.time() - t0
        if dt > 0:
            fps = 1.0 / dt
        t0 = time.time()

        text = f"{last_label} {last_prob:.3f} | {infer_ms:.0f}ms | {fps:.1f} FPS"
        cv2.putText(square, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.imshow("Detecting...", square)

        if (cv2.waitKey(1) & 0xFF) == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
