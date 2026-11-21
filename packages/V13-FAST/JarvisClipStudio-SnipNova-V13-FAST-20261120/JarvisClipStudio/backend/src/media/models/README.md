# Bundled models

## face_detection_yunet_2023mar.onnx

YuNet face detector, loaded through `cv2.FaceDetectorYN` in
`src/media/face_detection.py`.

- Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
  (downloaded from
  https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx)
- SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- License: MIT, Copyright (c) 2020 Shiqi Yu (see `LICENSE-yunet`)

The model lives under `src/` so it ships in the Docker image (`COPY src/`) and
in the docker-compose bind mount (`./backend/src:/app/src`).

## object_detection_yolox_2022nov_int8.onnx

YOLOX-s person detector (int8), loaded through `cv2.dnn` in
`src/media/person_detection.py`. It runs only on analysis samples where the
face detector found no significant face, as a subject-presence fallback for
people whose faces are hidden (helmets, backs of heads, night shots).

- Source: https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_yolox
  (downloaded from
  https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/object_detection_yolox/object_detection_yolox_2022nov_int8.onnx)
- SHA-256: `01a3b0f400b30bc1e45230e991b2e499ab42622485a330021947333fbaf03935`
- License: Apache-2.0 (see `LICENSE-yolox`; the zoo folder's README states
  all files in that directory are Apache-2.0, and YOLOX upstream — Megvii —
  is Apache-2.0 for code and weights)
