package com.insuranceai.android.ui
import com.insuranceai.android.data.*
import com.insuranceai.android.data.local.*
import com.insuranceai.android.sync.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow

/** Capture-only memory fixture. No repository, database, network, credentials or workers. */
internal class BlueCaptureModels(private val variant:String="normal") : CustomersModel, PoliciesModel, CoverageModel, ConsultationsModel, SyncModel {
 private val date="2026-01-15T09:00:00Z"
 override val customers=MutableStateFlow((if(variant=="empty") emptyList() else if(variant=="many") (1..24).map { "가상$it" } else listOf("가","나","다")).mapIndexed { i,n -> CustomerEntity(id="demo-${i+1}",name=if(variant=="long" && i==0) "데모고객 가 · 아주 긴 이름과 표시내용을 줄바꿈 검증하는 가상 고객" else "데모고객 $n",customerStatus="가입",syncStatus=0,createdAt=date,updatedAt=date) })
 override val policies=MutableStateFlow(customers.value.flatMap { c -> (1..(if(variant=="many") 24 else 1)).map { number -> val it=c; PolicyEntity(id="${it.id}-policy-$number",customerId=it.id,insurer="가상보험사",productName=if(variant=="long") "가상 보장 예시 · 아주 긴 보험사 상품명을 생략 없이 확인하는 계약명" else "가상 보장 예시 $number",premium=65000,paymentCycle="monthly",startDate="2026-01-01",endDate="2046-01-01",isOwn=true,syncStatus=0,createdAt=date,updatedAt=date) } })
 override val consultations=MutableStateFlow(customers.value.map { ConsultationEntity(id="${it.id}-consult",customerId=it.id,consultedAt=date,channel="방문",title="가상 보장 점검 상담",syncStatus=1,createdAt=date,updatedAt=date) })
 override val results=MutableStateFlow(customers.value.map { CoverageAnalysisEntity("${it.id}-analysis",it.id,"fixture-only",date,updatedAt=date) })
 override val analyses=results
 private val names=listOf("일반암 진단비","뇌혈관질환 진단비","허혈성심장질환 진단비","질병 입원일당")
 private val amounts=listOf(30000000L,20000000L,20000000L,30000L)
 override val coverages=MutableStateFlow(customers.value.flatMap { c -> names.mapIndexed { i,n -> PolicyCoverageEntity(id="${c.id}-coverage-$i",customerId=c.id,policyId="${c.id}-policy-1",insurer="가상보험사",productName="가상 보장 예시",riderName=n,standardName=n,amount=amounts[i],amountStatus="confirmed",rawPayloadEncrypted="fixture-only",updatedAt=date) } })
 override val familyRelationships=MutableStateFlow(emptyList<FamilyRelationshipEntity>())
 override val pendingCount=MutableStateFlow(consultations.value.size)
 override val pendingItems=MutableStateFlow(consultations.value.mapIndexed { i,c -> SyncOutboxEntity(localId=i.toLong()+1,operationId="demo-operation-$i",entityType="consultation",entityId=c.id,baseJson="{}",patchJson="{}",createdAt=date) })
 override val conflicts=MutableStateFlow(emptyList<SyncConflictEntity>())
 override val syncState=MutableStateFlow<SyncStateEntity?>(null)
 override val saveStatus=MutableStateFlow<String?>(null)
 override val saving=MutableStateFlow(false)
 override val status=MutableStateFlow("가상 데모 · 실제 PC 연결 없음")
 override val pairingState=MutableStateFlow(PairingUiState())
 override val recoveryState=MutableStateFlow(RecoveryUiState())
 override val conflictResolutionState=MutableStateFlow(ConflictResolutionUiState())
 override val addressContinuityState=MutableStateFlow(AddressContinuityUiState())
 override val registered=MutableStateFlow(false)
 override val registrationState=MutableStateFlow(ConnectionRegistrationState.UNREGISTERED)
 override val tlsBindingState=MutableStateFlow(TlsBindingState.NOT_REQUIRED)
 override val stagedRecoveryAvailable=MutableStateFlow(false)
 override val connectionLabel=MutableStateFlow("가상 데모 · PC 미연결")
 override fun editable(customer: CustomerEntity)=CustomerEditableFields(null,null,"가상 고객 메모 · 실제 개인정보 없음")
 override fun readableFields(policy: PolicyEntity):PolicyReadableFields?=null
 override fun parsed(item:CoverageAnalysisEntity)=if(variant=="failed") parseCoverageAnalysis("합성 형식 실패 입력 · 가상 데이터") else parseCoverageAnalysis("""{"summary":"가상 보장 예시 · 실제 분석 결과 아님","coverage_items":[{"coverage_name":"일반암 진단비","coverage_group":"암","current_amount":30000000,"recommended_amount":50000000,"status":"부족"},{"coverage_name":"뇌혈관질환 진단비","coverage_group":"뇌혈관질환","current_amount":20000000,"recommended_amount":20000000},{"coverage_name":"허혈성심장질환 진단비","coverage_group":"심장질환","current_amount":20000000,"recommended_amount":20000000},{"coverage_name":"질병 입원일당","coverage_group":"입원비(일당)·치료비","current_amount":30000,"recommended_amount":50000}]}""")
 override fun content(consultation:ConsultationEntity)="가상 상담 예시: 일반암 진단비 3천만원, 입원일당 3만원을 함께 확인했습니다. 실제 보험 추천이나 상담 기록이 아닙니다."
 private fun completed()=Job().apply{complete()}
 override fun saveAllowedEdit(customer:CustomerEntity,phone:String,address:String,memo:String,onSaved:()->Unit)=completed()
 override fun addConsultation(draft:ConsultationDraft,onSaved:()->Unit)=completed()
 override fun pair(endpoint:String,pin:String,code:String,requestAuthentication:UiAuthenticate)=completed()
 override fun recoverConnection(code:String,endpoint:String,pin:String,confirmedFromPc:Boolean,requestAuthentication:UiAuthenticate)=completed()
 override fun onPairingCodeChanged(code:String){}
 override fun updateConnectionAddress(endpoint:String,pin:String,confirmedFromPc:Boolean,requestAuthentication:UiAuthenticate)=completed()
 override fun syncNow()=completed()
 override fun resolve(conflict:SyncConflictEntity,chooseLocal:Boolean,requestAuthentication:UiAuthenticate)=completed()
 fun ports()=ScreenModels(this,this,this,this,this)
}
