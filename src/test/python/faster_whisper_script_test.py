# ruff: noqa: E402

from faster_whisper import WhisperModel
import os
import time

def test_faster_whisper(audio_path, model_size="small", device="cpu", compute_type="int8"):
    """
    测试 faster-whisper 的识别效果与性能
    """
    if not os.path.exists(audio_path):
        print(f"错误：文件 {audio_path} 不存在")
        return

    print("--- 测试配置 ---")
    print(f"模型大小: {model_size}")
    print(f"运行设备: {device}")
    print(f"计算类型: {compute_type}")
    print(f"音频路径: {audio_path}")
    print("----------------")

    start_time = time.time()
    
    # 初始化模型
    # download_root 可以指定模型下载路径，这里参考原项目的 models/ 目录
    model = WhisperModel(model_size, device=device, compute_type=compute_type, download_root="models/faster-whisper")
    
    load_time = time.time() - start_time
    print(f"模型加载耗时: {load_time:.2f}s")

    # 开始转录
    transcribe_start = time.time()
    # vad_filter=True 自动过滤静音，提高效率
    segments, info = model.transcribe(audio_path, beam_size=5, vad_filter=True)

    print(f"检测到语言: {info.language} (置信度: {info.language_probability:.2f})")
    print(f"音频总时长: {info.duration:.2f}s")
    print("--- 转录结果 ---")

    results = []
    for segment in segments:
        # 格式化时间戳 HH:MM:SS
        m, s = divmod(segment.start, 60)
        h, m = divmod(m, 60)
        timestamp = f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
        line = f"[{timestamp}] {segment.text}"
        print(line)
        results.append(line)

    transcribe_time = time.time() - transcribe_start
    total_time = time.time() - start_time

    print("----------------")
    print(f"转录耗时: {transcribe_time:.2f}s")
    print(f"实时率 (RTF): {transcribe_time / info.duration:.4f} (越小越快)")
    print(f"总耗时: {total_time:.2f}s")

if __name__ == "__main__":
    # 寻找一个现有的音频文件进行测试，或者提示用户提供
    # 检查 results 目录下是否有之前转换生成的临时音频
    test_file = "test.mp3" # 默认测试文件名
    
    # 尝试在 results 目录下找一个 wav 文件
    found_test_file = False
    if os.path.exists("results"):
        for root, dirs, files in os.walk("results"):
            for file in files:
                if file.endswith((".wav", ".mp3")):
                    test_file = os.path.join(root, file)
                    found_test_file = True
                    break
            if found_test_file:
                break

    if not found_test_file and not os.path.exists(test_file):
        # 尝试使用 icons 目录下的视频文件进行测试
        video_test = "icons/语音转换工具 2025-02-28 22-03-42.mp4"
        if os.path.exists(video_test):
            print(f"使用图标目录下的视频进行测试: {video_test}")
            test_faster_whisper(video_test, model_size="small")
        else:
            print("未找到测试音频文件，请确保当前目录下有 test.mp3 或 results 目录下有转换过的音频。")
    else:
        test_faster_whisper(test_file, model_size="small")
