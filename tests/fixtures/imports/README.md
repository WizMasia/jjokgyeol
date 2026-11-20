# T10 원본과 대조 기준

`SHA256SUMS`의 경로는 저장소 루트 기준이다. 시험은 입력 바이트와 디스크의 원본을 변환 전후에 검사한다. 대조 기준은 원본에서 읽은 값으로 고정했다.

| 표본 | 출처와 확인한 내용 |
| --- | --- |
| field.txt, field.html | 이 시험용으로 작성한 UTF-8 원고. 같은 ‘관측’이 TXT에서는 첫 문단의 첫 줄, HTML에서는 2단계 제목이다. HTML에는 링크와 원격 그림이 있다. |
| lists.docx | 번호 정의와 본문에서 ‘two → three → four’의 1·2·3단계와 ‘Some nested lists’의 제목 스타일을 확인했다. [Pandoc 원본](https://github.com/jgm/pandoc/blob/de38596d097cdfca456f7b916c6e32deaf6913ca/test/docx/lists.docx) |
| ParaHead.hwpx | 본문과 문단 모양에서 ‘번호3’의 두 자식, ‘번호4’ 다음의 1→4 점프, ‘개요2’부터 3단계 제목을 확인했다. [hwpxlib 원본](https://github.com/neolord0/hwpxlib/blob/f9fd2255ac0fc57414e0b657d115e7de51d31c65/testFile/tool/textextractor/ParaHead.hwpx) |
| lists.hwp, charshape.hwp, headerfooter.hwp, sample-5017.hwp | 공개된 HWP 5 원본과 같은 리비전의 formatted.xml. 목록·개요, 두 인접 강조 구간, 머리말·꼬리말, 표·그림·자동 번호가 있는 복합 문서를 대조한다. [pyhwp 원본 및 XML](https://github.com/mete0r/pyhwp/tree/83239f0d3bdf438b2c9f7dcff455a6e841154a39/tests/hwp5_tests/fixtures) |
| proofs 아래 DOCX·HWPX 6개 | 저장소 기준 9c5081d의 참고 문서·머리글·양면 여백 증빙. 본문 일곱 문단과 머리글 배제를 확인한다. HWPX 굵기 교정은 실물의 복사본에서 글자 속성 한 구간만 바꿔 따로 검사한다. |
