document.addEventListener('click',function(e){var b=e.target.closest('[data-confirm]');if(b&&!confirm(b.dataset.confirm))e.preventDefault()});
