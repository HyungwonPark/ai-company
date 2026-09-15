/* Reading language never supplies executable fields or confirmation digests. */
export function createPlanUI({esc, documents}) {
  function render(plan) {
    const content=plan.content||{}, id=`plan:${plan.id}`;
    const text=(field,original)=>esc(documents.text(id,field,original||''));
    const criteria=(values,field)=>values?.length?`<ul class="plan-criteria">${values.map((value,index)=>`<li>${text(`${field}:${index}`,value)}</li>`).join('')}</ul>`:'미등록';
    return `<section class="plan-reading" aria-label="계획 내용">${documents.meta(id)}<p class="report-summary">${text('summary',content.summary)}</p><div class="plan-roles">${(content.roles||[]).map((role,index)=>`<section class="role-config"><h3>${text(`role:${index}:name`,role.name||role.key)}</h3><p>${text(`role:${index}:responsibility`,role.responsibility)}</p><dl class="detail-grid"><div><dt>목표</dt><dd>${text(`role:${index}:goal`,role.goal)}</dd></div><div><dt>완료 조건</dt><dd>${criteria(role.acceptance,`role:${index}:acceptance`)}</dd></div><div><dt>허용 경로</dt><dd class="mono">${esc((role.allowed_paths||[]).join(' · '))}</dd></div><div><dt>선행 역할</dt><dd>${role.depends_on?.length?esc(role.depends_on.join(' · ')):'없음 · 독립 진행'}</dd></div><div><dt>역할 ID</dt><dd class="mono">${esc(role.key)}</dd></div></dl></section>`).join('')}</div><div class="plan-completion"><strong>완료 조건</strong>${criteria(content.completion_criteria,'completion')}</div></section>`;
  }
  return {render};
}
