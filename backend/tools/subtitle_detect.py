
import numpy as np

try:
    from paddleocr import TextDetection
except ImportError:
    TextDetection = None

from backend.tools.paddle_model_config import PaddleModelConfig
from backend.tools.hardware_accelerator import HardwareAccelerator


class SubtitleDetect:
    """
    文本框检测类，用于检测视频帧中是否存在文本框
    """

    def __init__(self):
        hardware_accelerator = HardwareAccelerator.instance()
        model_config = PaddleModelConfig(hardware_accelerator)
        # 使用 TextDetection 公开 API（PaddleOCR 3.x）
        if hardware_accelerator.has_cuda():
            device = 'gpu:0'
        else:
            device = 'cpu'
        kwargs = {'model_dir': model_config.DET_MODEL_PATH, 'device': device}
        if device == 'cpu':
            # PaddleOCR 3.x + Paddle 3.3 在部分 Windows CPU 环境下 oneDNN
            # 会触发 PIR 属性转换错误，CPU 路径显式关闭 MKLDNN。
            kwargs['enable_mkldnn'] = False
        if model_config.DET_MODEL_NAME:
            kwargs['model_name'] = model_config.DET_MODEL_NAME
        self.text_detector = TextDetection(**kwargs)

    def detect_subtitle(self, img):
        """
        检测图像中的文本框
        :param img: 输入图像
        :return: (dt_boxes, elapse) dt_boxes为numpy数组，elapse为耗时
        """
        results = list(self.text_detector.predict(img))
        if not results:
            return np.array([]), 0
        res = results[0]
        dt_polys = res.get('dt_polys', np.array([]))
        return dt_polys, 0
