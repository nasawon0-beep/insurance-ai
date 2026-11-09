package com.insuranceai.android

import org.junit.Assert.assertNotNull
import org.junit.Test

/**
 * phase9 회귀: biometric 1.1.0이 끌어오는 fragment 1.2.5의 FragmentActivity는 ActivityResultRegistry의 큰
 * requestCode를 "Can only use lower 16 bits for requestCode"로 거부해, QR 스캔 카메라 권한 요청 순간 앱이 종료됐다.
 * fragment 1.3.0부터 registry 경로를 그대로 쓰며, 같은 버전에 FragmentResultListener가 추가됐다.
 * 이 클래스가 없으면 classpath의 fragment가 다시 1.2.x로 내려간 것이다(실제 동작은 에뮬레이터에서 별도 확인).
 */
class ActivityResultCompatibilityTest {
    @Test
    fun fragmentLibraryIsNewEnoughForActivityResultPermissionLaunch() {
        assertNotNull(Class.forName("androidx.fragment.app.FragmentResultListener"))
    }
}
