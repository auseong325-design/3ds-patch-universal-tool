# RomForge 기반 추출·리팩 브리지

이 저장소는 [RomForge](https://github.com/sinjunyoung/RomForge) 커밋 `f7ba82439bc598c69c59e92b88dccffd627299da`의 로컬 포크입니다. 변경 사항은 `KpmBridge`, 합성 테스트 이미지 생성기 `KpmBridgeFixture`, 3DS 키 경로, 패치 적용 결과 노출, 복호화되지 않은 NCCH 및 CIA의 불필요한 키 처리, 구성 요소 해시 검증, CIA TMD 버전 전달입니다. 원본 RomForge 라이선스는 `LICENSE`와 `README.md`를 참조하세요. Translation Toolkit 쪽 소스는 별도 폴더에 있으며 각각의 라이선스를 유지합니다.

```powershell
dotnet build KpmBridge/KpmBridge.csproj -c Release -p:KpmBridge=true
```

브리지는 3DS 리팩 관련 소스만 연결합니다. `KpmBridge=true`에서는 저장소에 없는 실기 설치용 `custom-install-finalize.3dsx` 리소스를 제외하므로, 이 빌드는 실기 설치 기능을 제공하지 않습니다. 전체 RomForge 앱 빌드를 위한 대체 빌드가 아닙니다. .NET 10 SDK와 NuGet 복원 접근이 필요합니다. 실행에는 .NET 10 런타임이 필요합니다.

브리지는 표준 입력으로 JSON 요청을 받고 결과를 `KPM_RESULT:`로 시작하는 JSON 행으로 표준 출력에 내보냅니다. 사용자 UI는 Translation Toolkit 포크의 한국어 매니저입니다. 요청/응답은 내부 인터페이스이며 별도의 안정된 공개 API가 아닙니다. 키 파일을 배포하거나 소스 저장소에 포함하지 마세요.

테스트용 합성 이미지는 다음 명령으로 생성할 수 있습니다. 가짜 키와 인증서를 사용하는 인공 데이터로, 실제 게임이나 실기 호환성 검증 자료가 아닙니다.

```powershell
dotnet build KpmBridgeFixture/KpmBridgeFixture.csproj -c Release -p:KpmBridge=true
dotnet KpmBridgeFixture/bin/Release/net10.0/KpmBridgeFixture.dll <새-임시-폴더>
```
