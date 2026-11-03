import test from 'node:test';
import assert from 'node:assert/strict';
import {countLimitedPlan,toRects} from '../third_party/dsh-video-storyboard/src/storyboard/pack.js';
import {boardOptions} from '../third_party/dsh-video-storyboard/src/storyboard/board.js';
test('arbitrary per-sheet limits retain every frame in exact order, with valid geometry',()=>{
  for(const limit of [1,2,4,7,9,17,100,10000]){
    const tiers=Array.from({length:43},(_,i)=>i%7+1),plan=countLimitedPlan(tiers,limit);
    assert.deepEqual(plan.cells.map(c=>c.seq),tiers.map((_,i)=>i));
    for(let b=0;b<plan.bins;b++){
      const cells=plan.cells.filter(c=>c.bin===b);assert.ok(cells.length<=limit);assert.ok(cells.length>0);
      for(const c of cells){assert.ok(c.x+c.w<=1+1e-8);assert.ok(c.y+c.h<=1+1e-8);}
      const rects=toRects(plan.cells,{width:800,height:450,bin:b});assert.equal(rects.length,cells.length);
    }
  }
});
test('count limit reaches board, zoom and watch; invalid counts reject',()=>{
  for(const kind of ['board','zoom','watch'])assert.equal(boardOptions({input:'test.mp4',maxCellsPerSheet:4},{kind}).maxCellsPerSheet,4);
  for(const limit of [0,-1,1.5,10001,NaN])assert.throws(()=>countLimitedPlan([1,2],limit));
});
