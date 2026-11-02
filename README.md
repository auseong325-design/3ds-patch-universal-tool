# 3DS Korean Patch Manager / 3DS 한글패치 매니저

3DS 번역 패치의 제작과 적용을 돕는 도구입니다. 파일별 패치, Luma3DS·Azahar용 패키지, 업데이트와 번역·HOME 배너를 합친 게임 파일 만들기를 지원합니다. 처음 쓰는 사람을 위해 한국어 안내와 도움말을 제공합니다.

This tool helps creators and players prepare and apply 3DS translation patches. It supports file-level patches, Luma3DS and Azahar packages, and rebuilding a game with an update, translation and optional HOME banner. The interface and built-in help are currently in Korean.

## 시작하기 / Getting started

Windows: Python 3.10+와 Tkinter를 설치하고 `upstream/TranslationToolkit/3DS 한글패치 매니저 실행.bat`를 실행하세요. 리팩에는 .NET 10과 RomForge 브리지가, xdelta 패키지에는 xdelta3가 필요합니다. [상세 안내](upstream/TranslationToolkit/README_KO.md)

On Windows, install Python 3.10+ with Tkinter and run `upstream/TranslationToolkit/3DS 한글패치 매니저 실행.bat`. Repacking requires .NET 10 and the RomForge bridge; xdelta packages require xdelta3. See the [Korean guide](upstream/TranslationToolkit/README_KO.md) and [bridge build notes](RomForge-reference/README_KPM.md). A Windows portable build can be created using `packaging/build_portable.py`.

게임 파일, 번역 패치, 암호화 키와 인증서는 포함하지 않습니다. 일부 기능은 입력과 출력에 따라 키가 필요합니다. 실제 기기에서의 실행은 별도로 확인해야 합니다.

Game files, translation patches, encryption keys and certificates are not included. Some operations require user-provided keys. Successful rebuilding does not guarantee that a game will run on hardware.

## 출처와 감사 / Credits and thanks

이 프로젝트는 [Translation Toolkit](https://github.com/Ich73/TranslationToolkit)의 로컬 포크와 [RomForge](https://github.com/sinjunyoung/RomForge)의 로컬 포크를 바탕으로, 제작부터 사용자 적용까지 한 흐름으로 돕기 위해 만들었습니다. 기존 도구를 공개해 주신 제작자와 기여자분들께 깊이 감사드리며, 그 노력을 존중합니다. 번역 보조 코드에는 [BinJEditor](https://github.com/Ich73/BinJEditor)의 JTools가 포함됩니다.

This project builds on local forks of [Translation Toolkit](https://github.com/Ich73/TranslationToolkit) and [RomForge](https://github.com/sinjunyoung/RomForge), bringing patch creation and application into one workflow. We sincerely thank the original authors and contributors for sharing their work and respect the effort behind it. It also includes JTools from [BinJEditor](https://github.com/Ich73/BinJEditor).

## 라이선스 / Licenses

Translation Toolkit 및 JTools는 GPL-3.0, RomForge는 GPL-2.0-or-later입니다. 각 폴더의 라이선스와 제3자 구성요소 고지를 확인하세요. 배포용 런타임은 별도로 해당 라이선스를 동봉합니다.

Translation Toolkit and JTools are GPL-3.0; RomForge is GPL-2.0-or-later. See the individual license files and third-party notices for the relevant components.
