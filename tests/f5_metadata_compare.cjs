const {chromium}=require('playwright');
const path=require('path');
process.chdir(path.resolve(__dirname, '..'));
(async()=>{
 const browser=await chromium.launch({headless:true, channel:process.env.PLAYWRIGHT_CHANNEL || "chrome"}); const page=await browser.newPage();
 await page.route('https://example.com/cover.jpg', route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="120"><rect width="80" height="120" fill="navy"/></svg>'}));
 await page.setContent(`<input id="title" value="Original"><input id="authors" value="Author"><input id="publisher" value="Publisher"><input id="pubdate" value="2020-01-01"><input id="cover_url"><textarea id="comments">Old</textarea><input id="tags" value="Keep"><table id="identifier-table"><tbody><tr><td><input name="identifier-type-custom" value="ISBN"></td><td><input name="identifier-val-custom" value="old-isbn"></td></tr></tbody></table><div id="metaModal"></div><div id="metadata-compare"></div><div id="metadata_provider"></div><div id="meta-info"></div><input id="keyword"><button id="get_meta"></button><script type="text/template" id="template-book-result"><li><button class="metadata-compare-button">Compare</button></li></script>`);
 for(const file of ['jquery.min.js','underscore-umd-min.js']) await page.addScriptTag({path:'cps/static/js/libs/'+file});
 await page.evaluate(()=>{window.i18nMsg={};window.getPath=()=>'';$.fn.modal=function(){return this;};$.ajax=o=>o.success(o.url.includes('/provider')?[]:[{title:'Candidate',authors:['New author'],publisher:'New publisher',publishedDate:'2024-02-29',identifiers:{isbn:'new-isbn'},cover:'https://example.com/cover.jpg',description:'New description'}]);});
 await page.addScriptTag({path:'cps/static/js/get_meta.js'});
 await page.click('#get_meta'); await page.click('.metadata-compare-button');
 const checks=page.locator('#metadata-compare input[type=checkbox]');
 if(await checks.count()!==7 || await page.locator('#metadata-compare input:checked').count()) throw Error('Defaults');
 if(await page.inputValue('#title')!=='Original')throw Error('Eager mutation');
 await checks.nth(0).check();await checks.nth(4).check();await page.locator('#metadata-compare > button').first().click();
 if(await page.inputValue('#title')!=='Candidate'||await page.inputValue('[name=identifier-val-custom]')!=='new-isbn')throw Error('Apply');
 if(await page.inputValue('#authors')!=='Author'||await page.inputValue('#tags')!=='Keep')throw Error('Unselected mutation');
 await page.click('.metadata-compare-button');if(await page.locator('#metadata-compare input:checked').count())throw Error('Reset');
 await checks.nth(1).check();await page.locator('#metadata-compare > button').last().click();
 if(await page.inputValue('#authors')!=='Author')throw Error('Cancel');
 await page.click('.metadata-compare-button');
 for(let i=0;i<7;i++) await checks.nth(i).check();
 await page.locator('#metadata-compare > button').first().click();
 for(const [id,value] of Object.entries({authors:'New author',publisher:'New publisher',pubdate:'2024-02-29',comments:'New description',cover_url:'https://example.com/cover.jpg'})) if(await page.inputValue('#'+id)!==value)throw Error('All fields: '+id);
 await page.evaluate(()=>{
  window.i18nMsg.cover_unavailable='Cover unavailable';
  $.ajax=o=>o.success(o.url.includes('/provider')?[]:[{title:'Preview',authors:[],cover:'https://example.com/cover.jpg',description:'<p>First &amp; second</p><p>Next<br>Line</p><script>window.previewExecuted=true</script><img src="https://example.com/forbidden" onerror="window.previewExecuted=true">'}]);
 });
 let forbidden=0;page.on('request',request=>{if(request.url().includes('/forbidden'))forbidden++;});
 await page.click('#get_meta');await page.click('.metadata-compare-button');
 const cells=page.locator('#metadata-compare tbody tr').nth(6).locator('td');
 if(await cells.nth(2).innerText()!=='First & second\n\nNext\nLine')throw Error('Readable description');
 if(await cells.nth(2).locator('img,script').count()||await page.evaluate(()=>window.previewExecuted)||forbidden)throw Error('Active HTML preview');
 const images=page.locator('#metadata-compare tbody tr').nth(5).locator('img');
 await images.last().waitFor({state:'visible'});
 if(await images.count()!==2)throw Error('Side by side covers');
 await images.last().dispatchEvent('error');
 if(!await page.getByText('Cover unavailable',{exact:true}).last().isVisible())throw Error('Broken cover fallback');
 await checks.nth(6).check();await page.locator('#metadata-compare > button').first().click();
 if(!(await page.inputValue('#comments')).startsWith('<p>First &amp; second</p>'))throw Error('HTML original not preserved');
 await page.evaluate(()=>{
  document.querySelector('[name="identifier-type-custom"]').value='google';
  document.querySelector('[name="identifier-val-custom"]').value='keep-google';
  $.ajax=o=>o.success(o.url.includes('/provider')?[]:[{title:'ISBN candidate',authors:[],identifiers:{isbn:'9780000000002'}}]);
 });
 await page.click('#get_meta');await page.click('.metadata-compare-button');
 await checks.nth(4).check();await page.locator('#metadata-compare > button').first().click();
 if(await page.inputValue('[name="identifier-val-custom"]')!=='keep-google')throw Error('Unrelated identifier overwritten');
 if(await page.locator('#identifier-table tr').count()!==2)throw Error('Missing ISBN not added');
 if(await page.locator('#identifier-table tr').last().locator('input').last().inputValue()!=='9780000000002')throw Error('New ISBN value');
 await page.evaluate(()=>{$.ajax=o=>o.success(o.url.includes('/provider')?[]:[{title:'',authors:[],publishedDate:'2024-02-30',cover:'file:///bad',identifiers:{}}]);});
 await page.click('#get_meta');await page.click('.metadata-compare-button');
 if(await page.locator('#metadata-compare input:disabled').count()!==7)throw Error('Missing/invalid candidates');
 // Regression: cover preview remains available without upload permission.
 await page.evaluate(()=>{
  $('#cover_url').remove();
  i18nMsg.cover_permission='Preview only: upload permission required';
  i18nMsg.loading='Loading...';i18nMsg.no_result='No results';i18nMsg.search_error='Search failed';
  $.ajax=o=>o.success(o.url.includes('/provider')?[]:[{title:'Read-only cover',authors:[],cover:'https://example.com/cover.jpg'}]);
 });
 await page.click('#get_meta');await page.click('.metadata-compare-button');
 await page.locator('#metadata-compare tbody tr').nth(5).locator('img').waitFor({state:'visible'});
 if(!await checks.nth(5).isDisabled())throw Error('Cover permission not respected');
 if(!await page.getByText('Preview only: upload permission required',{exact:true}).isVisible())throw Error('Permission explanation absent');
 // Closing the modal clears a pending comparison without applying it.
 await checks.nth(0).check();
 const beforeClose=await page.inputValue('#title');
 await page.evaluate(()=>$('#metaModal').trigger('hidden.bs.modal'));
 if(await page.inputValue('#title')!==beforeClose || await checks.count())throw Error('Modal close mutation/reset');
 // Empty results and request errors must replace, not retain, loading text.
 await page.evaluate(()=>{$.ajax=o=>o.success([]);});
 await page.click('#get_meta');
 if(await page.locator('#meta-info').innerText()!=='No results')throw Error('Empty results retained loading');
 await page.evaluate(()=>{$.ajax=o=>o.url.includes('/provider')?o.success([]):o.error();});
 await page.click('#get_meta');
 if(await page.locator('#meta-info').innerText()!=='Search failed')throw Error('Error retained loading');
 // A provider enabled after an empty search can still add candidates.
 await page.evaluate(()=>{
  $('#metadata_provider').html('<input class="pill" data-control="fixture" data-initial="false" type="checkbox">');
  $.ajax=o=>o.success([{title:'Late candidate',authors:[]}]);
  $('.pill').prop('checked',true).trigger('change');
 });
 if(await page.locator('#book-list .metadata-compare-button').count()!==1)throw Error('Late provider results lost');
 console.log('PASS: permission-aware cover preview, modal close, loading cleanup and late provider results.');
 console.log('PASS: cover/description previews, original HTML, missing ISBN insertion and other identifier preservation; all seven applied, invalid/missing candidates disabled;  seven fields, unchecked defaults, preview without mutation, selective title/ISBN apply, untouched author/tags, selection reset, cancellation. Mock data; no API or database validation.');await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});


