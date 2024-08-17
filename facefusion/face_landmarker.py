from time import sleep
from typing import Optional, Tuple

import cv2
import numpy

from facefusion import process_manager, state_manager
from facefusion.download import conditional_download_hashes, conditional_download_sources
from facefusion.execution import create_inference_pool
from facefusion.face_helper import create_rotated_matrix_and_size, estimate_matrix_by_face_landmark_5, transform_points, warp_face_by_translation
from facefusion.filesystem import resolve_relative_path
from facefusion.thread_helper import conditional_thread_semaphore, thread_lock
from facefusion.typing import Angle, BoundingBox, DownloadSet, FaceLandmark5, FaceLandmark68, InferencePool, ModelSet, Score, VisionFrame

INFERENCE_POOL : Optional[InferencePool] = None
MODEL_SET : ModelSet =\
{
	'2dfan4':
	{
		'hashes':
		{
			'2dfan4':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0-optraw/2dfan4.hash',
				'path': resolve_relative_path('../.assets/models-optraw/2dfan4.hash')
			}
		},
		'sources':
		{
			'2dfan4':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0-optraw/2dfan4.onnx',
				'path': resolve_relative_path('../.assets/models-optraw/2dfan4.onnx')
			}
		}
	},
	'peppa_wutz':
	{
		'hashes':
		{
			'peppa_wutz':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0-optraw/peppa_wutz.hash',
				'path': resolve_relative_path('../.assets/models-optraw/peppa_wutz.hash')
			}
		},
		'sources':
		{
			'peppa_wutz':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0-optraw/peppa_wutz.onnx',
				'path': resolve_relative_path('../.assets/models-optraw/peppa_wutz.onnx')
			}
		}
	},
	'face_landmarker_68_5':
	{
		'hashes':
		{
			'face_landmarker_68_5':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0-optraw/face_landmarker_68_5.hash',
				'path': resolve_relative_path('../.assets/models-optraw/face_landmarker_68_5.hash')
			}
		},
		'sources':
		{
			'face_landmarker_68_5':
			{
				'url': 'https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0-optraw/face_landmarker_68_5.onnx',
				'path': resolve_relative_path('../.assets/models-optraw/face_landmarker_68_5.onnx')
			}
		}
	}
}


def get_inference_pool() -> InferencePool:
	global INFERENCE_POOL

	with thread_lock():
		while process_manager.is_checking():
			sleep(0.5)
		if INFERENCE_POOL is None:
			_, model_sources = collect_model_downloads()
			INFERENCE_POOL = create_inference_pool(model_sources, state_manager.get_item('execution_device_id'), state_manager.get_item('execution_providers'))
		return INFERENCE_POOL


def clear_inference_pool() -> None:
	global INFERENCE_POOL

	INFERENCE_POOL = None


def collect_model_downloads() -> Tuple[DownloadSet, DownloadSet]:
	model_hashes =\
	{
		'face_landmarker_68_5': MODEL_SET.get('face_landmarker_68_5').get('hashes').get('face_landmarker_68_5')
	}
	model_sources =\
	{
		'face_landmarker_68_5': MODEL_SET.get('face_landmarker_68_5').get('sources').get('face_landmarker_68_5')
	}

	if state_manager.get_item('face_landmarker_model') in [ 'many', '2dfan4' ]:
		model_hashes['2dfan4'] = MODEL_SET.get('2dfan4').get('hashes').get('2dfan4')
		model_sources['2dfan4'] = MODEL_SET.get('2dfan4').get('sources').get('2dfan4')
	if state_manager.get_item('face_landmarker_model') in [ 'many', 'peppa_wutz' ]:
		model_hashes['peppa_wutz'] = MODEL_SET.get('peppa_wutz').get('hashes').get('peppa_wutz')
		model_sources['peppa_wutz'] = MODEL_SET.get('peppa_wutz').get('sources').get('peppa_wutz')
	return model_hashes, model_sources


def pre_check() -> bool:
	download_directory_path = resolve_relative_path('../.assets/models-optraw')
	model_hashes, model_sources = collect_model_downloads()

	return conditional_download_hashes(download_directory_path, model_hashes) and conditional_download_sources(download_directory_path, model_sources)


def detect_face_landmarks(vision_frame : VisionFrame, bounding_box : BoundingBox, face_angle : Angle) -> Tuple[FaceLandmark68, Score]:
	face_landmark_2dfan4 = None
	face_landmark_peppa_wutz = None
	face_landmark_score_2dfan4 = 0.0
	face_landmark_score_peppa_wutz = 0.0

	if state_manager.get_item('face_landmarker_model') in [ 'many', '2dfan4' ]:
		face_landmark_2dfan4, face_landmark_score_2dfan4 = detect_with_2dfan4(vision_frame, bounding_box, face_angle)
	if state_manager.get_item('face_landmarker_model') in [ 'many', 'peppa_wutz' ]:
		face_landmark_peppa_wutz, face_landmark_score_peppa_wutz = detect_with_peppa_wutz(vision_frame, bounding_box, face_angle)

	if face_landmark_score_2dfan4 > face_landmark_score_peppa_wutz:
		return face_landmark_2dfan4, face_landmark_score_2dfan4
	return face_landmark_peppa_wutz, face_landmark_score_peppa_wutz


def detect_with_2dfan4(temp_vision_frame : VisionFrame, bounding_box : BoundingBox, face_angle : Angle) -> Tuple[FaceLandmark68, Score]:
	face_landmarker = get_inference_pool().get('2dfan4')
	scale = 195 / numpy.subtract(bounding_box[2:], bounding_box[:2]).max().clip(1, None)
	translation = (256 - numpy.add(bounding_box[2:], bounding_box[:2]) * scale) * 0.5
	rotated_matrix, rotated_size = create_rotated_matrix_and_size(face_angle, (256, 256))
	crop_vision_frame, affine_matrix = warp_face_by_translation(temp_vision_frame, translation, scale, (256, 256))
	crop_vision_frame = cv2.warpAffine(crop_vision_frame, rotated_matrix, rotated_size)
	crop_vision_frame = conditional_optimize_contrast(crop_vision_frame)
	crop_vision_frame = crop_vision_frame.transpose(2, 0, 1).astype(numpy.float32) / 255.0

	with conditional_thread_semaphore():
		face_landmark_68, face_heatmap = face_landmarker.run(None,
		{
			'input': [ crop_vision_frame ]
		})

	face_landmark_68 = face_landmark_68[:, :, :2][0] / 64 * 256
	face_landmark_68 = transform_points(face_landmark_68, cv2.invertAffineTransform(rotated_matrix))
	face_landmark_68 = transform_points(face_landmark_68, cv2.invertAffineTransform(affine_matrix))
	face_landmark_score_68 = numpy.amax(face_heatmap, axis = (2, 3))
	face_landmark_score_68 = numpy.mean(face_landmark_score_68)
	return face_landmark_68, face_landmark_score_68


def detect_with_peppa_wutz(temp_vision_frame : VisionFrame, bounding_box : BoundingBox, face_angle : Angle) -> Tuple[FaceLandmark68, Score]:
	face_landmarker = get_inference_pool().get('peppa_wutz')
	scale = 195 / numpy.subtract(bounding_box[2:], bounding_box[:2]).max().clip(1, None)
	translation = (256 - numpy.add(bounding_box[2:], bounding_box[:2]) * scale) * 0.5
	rotated_matrix, rotated_size = create_rotated_matrix_and_size(face_angle, (256, 256))
	crop_vision_frame, affine_matrix = warp_face_by_translation(temp_vision_frame, translation, scale, (256, 256))
	crop_vision_frame = cv2.warpAffine(crop_vision_frame, rotated_matrix, rotated_size)
	crop_vision_frame = conditional_optimize_contrast(crop_vision_frame)
	crop_vision_frame = crop_vision_frame.transpose(2, 0, 1).astype(numpy.float32) / 255.0
	crop_vision_frame = numpy.expand_dims(crop_vision_frame, axis = 0)

	with conditional_thread_semaphore():
		prediction = face_landmarker.run(None,
		{
			'input': crop_vision_frame
		})[0]

	face_landmark_68 = prediction.reshape(-1, 3)[:, :2] / 64 * 256
	face_landmark_68 = transform_points(face_landmark_68, cv2.invertAffineTransform(rotated_matrix))
	face_landmark_68 = transform_points(face_landmark_68, cv2.invertAffineTransform(affine_matrix))
	face_landmark_score_68 = prediction.reshape(-1, 3)[:, 2].mean()
	return face_landmark_68, face_landmark_score_68


def conditional_optimize_contrast(crop_vision_frame : VisionFrame) -> VisionFrame:
	crop_vision_frame = cv2.cvtColor(crop_vision_frame, cv2.COLOR_RGB2Lab)
	if numpy.mean(crop_vision_frame[:, :, 0]) < 30:  # type:ignore[arg-type]
		crop_vision_frame[:, :, 0] = cv2.createCLAHE(clipLimit = 2).apply(crop_vision_frame[:, :, 0])
	crop_vision_frame = cv2.cvtColor(crop_vision_frame, cv2.COLOR_Lab2RGB)
	return crop_vision_frame


def estimate_face_landmark_68_5(face_landmark_5 : FaceLandmark5) -> FaceLandmark68:
	face_landmarker = get_inference_pool().get('face_landmarker_68_5')
	affine_matrix = estimate_matrix_by_face_landmark_5(face_landmark_5, 'ffhq_512', (1, 1))
	face_landmark_5 = cv2.transform(face_landmark_5.reshape(1, -1, 2), affine_matrix).reshape(-1, 2)

	with conditional_thread_semaphore():
		face_landmark_68_5 = face_landmarker.run(None,
		{
			'input': [ face_landmark_5 ]
		})[0][0]

	face_landmark_68_5 = cv2.transform(face_landmark_68_5.reshape(1, -1, 2), cv2.invertAffineTransform(affine_matrix)).reshape(-1, 2)
	return face_landmark_68_5
