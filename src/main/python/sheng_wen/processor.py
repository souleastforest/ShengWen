from .transcriber.transcriber import Transcriber, TranscriberError
from .llm.llm import LLM, LLMMessage, LLMError
from typing import Callable, Union

class ShengWenProcessor:
    """
    核心业务逻辑处理器。
    它将转录器和LLM客户端组合在一起，完成从音频到结构化文本的完整流程。
    """
    def __init__(self, transcriber: Transcriber, llm_client: LLM):
        """
        初始化处理器。

        此构造函数接收已经实例化的 `transcriber` 和 `llm_client` 对象，
        遵循依赖注入原则，使得 `ShengWenProcessor` 与具体实现解耦。

        参数:
            transcriber: 一个实现了 Transcriber 接口的对象。
            llm_client: 一个实现了 LLM 接口的对象。
        """
        self.transcriber = transcriber
        self.llm_client = llm_client
        self.system_prompt = ""

    def load_system_prompt(self, prompt_path: str):
        """
        从文件加载系统提示词。

        参数:
            prompt_path: 提示词文件的路径。
        """
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                self.system_prompt = f.read()
            print(f"成功加载系统提示词: {prompt_path}")
        except FileNotFoundError:
            print(f"警告: 未找到系统提示词文件: {prompt_path}。将使用空提示词。")
            self.system_prompt = ""

    async def process_audio(
        self,
        audio_path: str,
        output_path: str,
        llm_callback: Callable[[Union[str, LLMError]], None]
    ):
        """
        处理单个音频文件的完整流程。

        1. 使用 fast-whisper 转录音频。
        2. 加载系统提示词。
        3. 将系统提示词和转录内容发送给 LLM。
        4. 通过回调实时返回 LLM 的流式响应。
        5. 将最终的 LLM 输出保存到文件。

        参数:
            audio_path: 输入的音频文件路径。
            output_path: 保存最终 LLM 输出的文件路径。
            llm_callback: 用于接收 LLM 实时响应或错误的回调函数。
        """
        print(f"--- 开始处理音频文件: {audio_path} ---")
        
        # 1. 转录音频
        try:
            print("步骤 1/4: 正在转录音频...")
            transcription_result = self.transcriber.transcribe(audio_path)
            transcribed_text = " ".join([seg['text'] for seg in transcription_result.segments])
            print(f"转录完成。音频时长: {transcription_result.audio_duration:.2f}s, 耗时: {transcription_result.transcription_time:.2f}s")
        except TranscriberError as e:
            print(f"错误: 音频转录失败: {e}")
            llm_callback(LLMError(f"音频转录失败: {e}"))
            return

        # 2. 准备 LLM 输入
        if not self.system_prompt:
            print("警告: 系统提示词为空。")
        
        messages = [
            LLMMessage(role="system", content=self.system_prompt),
            LLMMessage(role="user", content=f"""这是需要处理的语音转录文本：{transcribed_text}""")
        ]

        # 3. & 4. 调用 LLM 并实时返回结果
        print("步骤 2/4: 正在调用大语言模型...")
        full_response = []
        
        # 创建一个内部回调来收集数据，同时调用外部回调
        def internal_callback(chunk: Union[str, LLMError]):
            if isinstance(chunk, str):
                full_response.append(chunk)
            llm_callback(chunk) # 将数据实时传递给外部

        await self.llm_client.response(messages, internal_callback, stream=True)
        
        final_text = "".join(full_response)

        if not final_text.strip():
            print("警告: LLM 返回了空响应。")
            return

        # 5. 保存最终结果
        print(f"步骤 3/4: 正在将最终结果保存到 {output_path}...")
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(final_text)
            print("步骤 4/4: 处理完成！")
        except IOError as e:
            error = LLMError(f"无法写入输出文件: {e}")
            print(f"错误: {error}")
            llm_callback(error)


