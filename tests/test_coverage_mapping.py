from database.coverage_mapping import normalize_coverage_name


class TestCoverageMapping:
    def test_normalize_일반암(self):
        assert normalize_coverage_name("암진단비") == "일반암진단비"
        assert normalize_coverage_name("일반암") == "일반암진단비"
        assert normalize_coverage_name("암보험금") == "일반암진단비"

    def test_normalize_뇌혈관(self):
        assert normalize_coverage_name("뇌혈관질환진단비") == "뇌혈관진단비"
        assert normalize_coverage_name("뇌졸중") == "뇌졸중진단비"
        assert normalize_coverage_name("뇌출혈") == "뇌졸중진단비"

    def test_normalize_수술(self):
        assert normalize_coverage_name("질병으로인한수술") == "질병수술비"
        assert normalize_coverage_name("재해수술") == "상해수술비"
