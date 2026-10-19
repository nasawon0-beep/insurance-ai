from database.coverage_mapping import normalize_coverage_name


class TestCoverageMapping:
    def test_normalize_일반암(self):
        assert normalize_coverage_name("암진단비") == "일반암진단비"
        assert normalize_coverage_name("일반암") == "일반암진단비"
        assert normalize_coverage_name("암보험금") == "일반암진단비"

    def test_normalize_뇌혈관(self):
        assert normalize_coverage_name("뇌혈관질환진단비") == "뇌혈관진단비"
        assert normalize_coverage_name("뇌졸중") == "뇌졸중진단비"
        assert normalize_coverage_name("뇌출혈") == "뇌출혈진단비"

    def test_normalize_수술(self):
        assert normalize_coverage_name("질병으로인한수술") == "질병수술비"
        assert normalize_coverage_name("재해수술") == "상해수술비"
        assert normalize_coverage_name("질병종수술") == "질병종수술"
        assert normalize_coverage_name("상해종수술") == "상해종수술"
        assert normalize_coverage_name("질병1~5종수술비") == "질병종수술"
        assert normalize_coverage_name("상해1-5종수술비") == "상해종수술"

    def test_normalize_입원의료비와_일당은_서로_다르다(self):
        assert normalize_coverage_name("질병입원의료비") == "질병입원"
        assert normalize_coverage_name("질병입원일당") == "질병일당"
        assert normalize_coverage_name("상해입원의료비") == "상해입원"
        assert normalize_coverage_name("상해입원일당") == "상해일당"
