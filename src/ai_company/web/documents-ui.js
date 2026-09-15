/* Read-only language views. Source fields and approval identity are never rewritten. */
export function createDocumentUI({esc,readable,stamp,getOverview}) {
  const modes = new Map();
  const statusNames = {completed:'한국어 번역',not_required:'한국어 원문',pending:'번역 대기',running:'번역 중',waiting_quota:'번역 사용량 대기',waiting_retry:'번역 재시도 대기',blocked:'번역 차단',failed:'번역 실패',stale:'원문 갱신 · 이전 번역',unconfigured:'번역 미설정'};
  const find = id => getOverview()?.documents?.[id];
  const valid = doc => Boolean(doc?.translation?.status==='completed'&&doc.translation.source_digest===doc.source_digest&&doc.translation.id);
  const key = doc => `${doc.project_id}:${doc.id}:${doc.source_digest}`;
  const original = doc => modes.get(key(doc))==='original';
  function text(id,field,fallback='') {
    const doc=find(id);
    if(!doc)return fallback;
    if(valid(doc)&&!original(doc)&&typeof doc.translation.fields?.[field]==='string')return doc.translation.fields[field];
    return doc.fields?.[field]??fallback;
  }
  function translationRef(id) {
    const doc=find(id);
    return valid(doc)&&!original(doc)?{id:doc.translation.id,source_digest:doc.source_digest}:null;
  }
  function meta(id) {
    const doc=find(id);if(!doc)return '';
    const t=doc.translation;
    const status=t&&t.source_digest!==doc.source_digest?'stale':t?.status||(doc.original_language==='ko'?'not_required':'unconfigured');
    const translated=valid(doc)&&!original(doc);
    return `<section class="document-language" data-document-id="${esc(id)}" aria-label="문서 언어와 출처"><div class="document-language-bar"><span class="badge ${translated?'complete':''}">${esc(statusNames[status]||status)}</span><span>${translated?'한국어 표시':status==='not_required'?'한국어 원문 표시':'원문 표시'}</span>${valid(doc)?`<button type="button" class="quiet document-toggle" data-document-toggle="${esc(id)}" aria-pressed="${original(doc)}">${original(doc)?'한국어 보기':'원문 보기'}</button>`:''}</div><details data-persist-key="doc:${esc(id)}"><summary>원문·번역 출처 확인</summary><dl class="detail-grid"><div><dt>원문 작성 역할</dt><dd>${esc(doc.author_role||'미확인')}</dd></div><div><dt>원문 버전 / 언어</dt><dd>${esc(doc.source_version)} / ${esc(doc.original_language||'미확인')}</dd></div><div><dt>원문 식별값</dt><dd class="mono">${esc(doc.source_digest)}</dd></div><div><dt>원본 근거</dt><dd class="mono">${esc(readable(doc.source_ref))}</dd></div>${t?`<div><dt>번역 모델 / 버전</dt><dd>${esc(t.model||'미확인')} / ${esc(t.version??'미확인')}</dd></div><div><dt>번역 산출물</dt><dd class="mono">${esc(t.id||'없음')}</dd></div>`:''}</dl>${t?.reason?`<p class="small muted">${esc(t.reason)}</p>`:''}${t?.substitution_reason?`<p class="small muted">번역 담당 모델 변경 사유 · ${esc(t.substitution_reason)}</p>`:''}${t?.resume_at?`<p class="small">번역 재확인 예약 · ${stamp(t.resume_at)}</p>`:''}${t?.requested_configuration||t?.observed_configuration?`<details><summary>번역 요청·관측 설정</summary><div class="verification">요청: ${esc(readable(t.requested_configuration))}\n관측: ${esc(readable(t.observed_configuration))}</div></details>`:''}${t?.reprocessing?`<div class="translation-reprocessing"><p class="small">실제 CLI 저장 기록 재처리 · 원기록 ${esc(t.reprocessing.original_failed_status||'상태 미확인')} 유지 · 추가 모델 호출 ${esc(t.reprocessing.new_model_calls??'미확인')}회</p><dl class="detail-grid"><div><dt>원기록 실패 이유</dt><dd>${esc(t.reprocessing.original_failure_reason||'미확인')}</dd></div><div><dt>재처리 파서</dt><dd>${esc(t.reprocessing.parser_version||t.parser_version||'미확인')}</dd></div><div><dt>원시 출력 식별값</dt><dd class="mono">${esc(t.reprocessing.raw_sha256||'미확인')}</dd></div></dl></div>`:''}${t?.semantic_validation?`<p class="small muted">번역 의미 검증: ${esc(t.semantic_validation==='not_independently_verified'?'독립 검증 전':t.semantic_validation)}</p>`:''}<p class="small muted">번역은 읽기 위한 별도 산출물이며 작성자·검수자·승인 권한을 바꾸지 않습니다.</p><details><summary>원문 전체 보기</summary><div class="verification original-document">${esc(Object.entries(doc.fields||{}).map(([field,value])=>`${field}\n${value}`).join('\n\n'))}</div></details></details></section>`;
  }
  function toggle(id){const doc=find(id);if(!valid(doc))return false;modes.set(key(doc),original(doc)?'korean':'original');return true;}
  function clear(){modes.clear();}
  return {text,meta,translationRef,toggle,clear,find};
}
