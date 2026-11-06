// Run: node tests/check_branding.mjs
import assert from 'node:assert/strict';
import { applyBranding, brandingPalette, brandingLogo, brandingName,
         normalizeAccent } from '../pos/static/js/branding.js';

assert.equal(normalizeAccent('#ab1234'), '#AB1234');
assert.equal(normalizeAccent('red; background:evil'), '#1B6BB5');
assert.equal(brandingName({pos_display_name:'My Store'}), 'My Store');
assert.equal(brandingName({}), 'pyPOS');
const logo = 'data:image/png;base64,aGVsbG8=';
assert.equal(brandingLogo({pos_logo_mode:'custom',pos_logo:logo}),logo);
assert.equal(brandingLogo({pos_logo_mode:'none',pos_logo:logo}), '');
assert.equal(brandingLogo({pos_logo_mode:'receipt',store_logo:logo}),logo);
assert.equal(brandingLogo({pos_logo_mode:'custom',pos_logo:'javascript:alert(1)'}),'');
for (const color of ['#F8DB54', '#030304', '#1B6BB5', '#40F0C0']) {
  for (const dark of [true,false]) {
    const tokens = brandingPalette(color, dark);
    for (const name of ['--accent','--accent-soft','--cta-grad-top','--cta-text']) {
      assert.match(tokens[name], /^#[0-9A-F]{6}$/i, `invalid ${name}: ${tokens[name]}`);
    }
    assert.equal(tokens['--brand-color'],color);
  }
}

// A minimal DOM exercise catches broken DOM IDs and missing theme updates.
const props = {};
const brandName = {textContent:'pyPOS',title:''};
const mark = {hidden:false,classList:{add(){},remove(){}},
              replaceChildren(...items){this.children=items;},set textContent(x){this.text=x;}};
const icon = {href:''};
globalThis.document = {
  documentElement: {dataset:{theme:'light'},style:{setProperty(key,value){props[key]=value;}}},
  getElementById(id){return {'app-brand-name':brandName,'app-brand-mark':mark,'app-favicon':icon}[id];},
  createElement(tag){return {tag};}, title:'',
};
applyBranding({pos_display_name:'Kolkata Retail',pos_accent_color:'#A52A4A',pos_logo_mode:'none'});
assert.equal(brandName.textContent,'Kolkata Retail');
assert.equal(document.title,'Kolkata Retail');
assert.equal(mark.hidden,true);
assert.match(icon.href,/^data:image\/svg\+xml,/);
const before = props['--accent'];
applyBranding({pos_accent_color:'#A52A4A'}, 'dark');
assert.notEqual(props['--accent'],before);
console.log('PASS: store branding tokens, logo source, DOM updates and theme adaptation');
