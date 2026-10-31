"""判题语言配置：接口、DockerRunner 与 go-judge 共用同一份定义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageSpec:
    key: str
    label: str
    filename: str
    artifact: str

    def compile_command(self, filename: str | None = None, *, sanitize: bool = False) -> list[str]:
        source = filename or self.filename
        if self.key == "cpp17":
            flags = (
                ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
                if sanitize else ["-O2"]
            )
            return ["/usr/bin/g++", *flags, "-std=c++17", "-Wall", "-o", "main", source]
        if self.key == "c11":
            flags = (
                ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
                if sanitize else ["-O2"]
            )
            return ["/usr/bin/gcc", *flags, "-std=c11", "-Wall", "-o", "main", source]
        if self.key == "java17":
            return [
                "/bin/bash", "-lc",
                f"/usr/bin/javac -encoding UTF-8 {source} && /usr/bin/jar cf main.jar Main*.class",
            ]
        return ["/usr/bin/python3", "-m", "py_compile", source]

    def run_command(self, memory_limit_mb: int) -> list[str]:
        if self.key in ("cpp17", "c11"):
            return ["./main"]
        if self.key == "java17":
            heap_mb = max(32, memory_limit_mb - 96)
            return [
                "/usr/bin/java", f"-Xmx{heap_mb}m", "-Dfile.encoding=UTF-8",
                "-cp", "main.jar", "Main",
            ]
        return ["/usr/bin/python3", "-B", "main.py"]


LANGUAGES = {
    "cpp17": LanguageSpec("cpp17", "C++17", "main.cpp", "main"),
    "c11": LanguageSpec("c11", "C11", "main.c", "main"),
    "java17": LanguageSpec("java17", "Java 17", "Main.java", "main.jar"),
    "python3": LanguageSpec("python3", "Python 3", "main.py", "main.py"),
}
SUPPORTED_LANGUAGES = tuple(LANGUAGES)


def language_spec(language: str) -> LanguageSpec:
    try:
        return LANGUAGES[language]
    except KeyError as exc:
        raise ValueError(f"不支持的编程语言: {language}") from exc
