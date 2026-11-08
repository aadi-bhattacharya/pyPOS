/* Tiny enhancement for locally served auth pages. No inline scripts or network. */
document.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-reveal]');
  if (!button) return;
  const field = document.getElementById(button.dataset.reveal);
  if (!field || (field.type !== 'password' && field.type !== 'text')) return;
  const showing = field.type === 'password';
  field.type = showing ? 'text' : 'password';
  button.textContent = showing ? 'Hide' : 'Show';
  button.setAttribute('aria-pressed', String(showing));
  button.setAttribute('aria-label', `${showing ? 'Hide' : 'Show'} ${field.id.includes('pin') ? 'register PIN' : 'administrator password'}`);
  field.focus({preventScroll: true});
});
