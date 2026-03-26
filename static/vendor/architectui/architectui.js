document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.querySelector('[data-app-sidebar-toggle]');
  const sidebar = document.querySelector('[data-app-sidebar]');

  if (!toggle || !sidebar) {
    return;
  }

  toggle.addEventListener('click', () => {
    sidebar.classList.toggle('is-open');
  });
});
