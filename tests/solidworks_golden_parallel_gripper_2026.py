"""@brief P0-2：真实 SW2026 双指夹爪结构 Golden；无驱动机构、无替代后端。"""
from __future__ import annotations
import csv
import gc
import json
import math
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from evals.runner import load_golden_workflows
from solidworks_golden_mounting_bracket_2026 import snapshot_model, close_enough as near, write_json, sha256_file
from golden_workflow_support import resolve_material_database, process_created_at, VIEWS
import golden_workflow_harness as harness

CASE_ID='parallel_gripper_assembly'
SNAPSHOT_KEYS=('parts','built','reopened','step_reopened','native_fault_controls')


def load_spec():
    """@brief 从唯一 Golden 定义读取规格，不在 CAD 运行配置中改变验收要求。"""
    return next(w['real_acceptance_2026'] for w in load_golden_workflows()['workflows'] if w['id']==CASE_ID)


def part_properties(part):
    """@brief 解析体积和质心；孔按真实圆柱扣除，不使用 API 创建成功值。"""
    if 'diameter_mm' in part:
        return math.pi*(part['diameter_mm']/2)**2*part['length_mm'], [0,0,part['length_mm']/2]
    x,y,z=part['size_mm']
    gross=x*y*z
    holes=part.get('hole_centers_mm',[])
    removed=math.pi*(part.get('hole_diameter_mm',0)/2)**2*z
    volume=gross-len(holes)*removed
    center=[(gross*part['center_xy_mm'][i]-sum(p[i]*removed for p in holes))/volume for i in range(2)]
    return volume,[*center,z/2]


def mate_contract():
    """@brief 22 个真实 Mate；双导杆各自参与滑块同心约束，不依赖 LockRotation 隐式锁角。"""
    result=[]
    for role,other in [('support_left','base'),('support_right','base'),('finger_left','slider_left'),('finger_right','slider_right')]:
        for plane in ('Right Plane','Top Plane','Front Plane'):
            distance=128 if role=='support_right' and plane=='Front Plane' else 0
            result.append({'name':role+'_'+plane.split()[0], 'roles':[role,other], 'type':5 if distance else 0,
                           'plane':plane,'distance_mm':distance,'flip':bool(distance)})
    for role in ('rod_left','rod_right'):
        result.extend([{'name':role+'_guide','roles':[role,'support_left'],'type':1,'lock_rotation':False,
                        'left_cylinder_x_mm':0,'right_cylinder_x_mm':-18 if role.endswith('left') else 18},
                       {'name':role+'_axial','roles':[role,'base'],'type':0,'plane':'Front Plane','distance_mm':0},
                       {'name':role+'_clock','roles':[role,'base'],'type':3,'plane':'Top Plane','distance_mm':0}])
    for role in ('slider_left','slider_right'):
        for suffix,rod,x in (('guide','rod_left',-18),('guide_right','rod_right',18)):
            result.append({'name':role+'_'+suffix,'roles':[role,rod],'type':1,'lock_rotation':False,
                           'left_cylinder_x_mm':x,'right_cylinder_x_mm':0})
    return result


def extra_evidence():
    """@brief 固定要求全部五个原生零件、BOM、规格及工程阶段计划。"""
    return {'parts.json','native_fault_controls.json','bom.csv','spec.json','engineering_plan.json',*(f'parts/{p}.SLDPRT' for p in load_spec()['parts'])}


def validate_part(snapshot, part, spec):
    """@brief 回读真实实体、参数、孔径孔位/深度、草图与材料，任一未知即失败。"""
    volume,_=part_properties(part)
    size=part.get('size_mm',[part.get('diameter_mm'),part.get('diameter_mm'),part.get('length_mm')])
    geometry=snapshot['geometry']
    checks={'one_solid':snapshot['body_count']==1,'measurements':not geometry.get('errors') and not snapshot['mass'].get('errors'),
        'volume':near(snapshot['mass'].get('volume_mm3'),volume,spec['volume_tolerance_mm3']),
        'dimensions':all(near(geometry.get('envelope_mm',{}).get(k),v,spec['length_tolerance_mm']) for k,v in zip(('length','width','height'),size)),
        'editable_depth':near(snapshot.get('thickness_mm'),size[2],spec['length_tolerance_mm']),
        'material':snapshot['material'].get('name')==spec['material_entry'] and bool(snapshot['material'].get('database')),
        'material_density':near(snapshot['mass'].get('density_kg_m3'),2700,1),
        'sketches':snapshot['sketch_quality'].get('status')=='pass' and len(snapshot['sketch_quality'].get('sketches',[]))==(2 if part.get('hole_centers_mm') else 1),
        'feature_health':not any(snapshot['features'].get(k) for k in ('feature_error','faulty_features','warning_features','unknown_feature_health'))}
    types=[f.get('underlying_type',f['type']) for f in snapshot['features']['features']]
    checks['native_features']=sum(t in ('Boss','Extrusion') for t in types)==1 and types.count('Cut')==(1 if part.get('hole_centers_mm') else 0)
    holes=geometry.get('holes',[])
    checks['hole_count']=len(holes)==len(part.get('hole_centers_mm',[]))
    for i,(x,y) in enumerate(part.get('hole_centers_mm',[])):
        checks[f'hole_{i}']=sum(near(h.get('diameter_mm'),part['hole_diameter_mm'],.01)
            and near(h['position_mm'][0],x,.01) and near(h['position_mm'][1],y,.01)
            and near(h.get('axial_length_mm'),size[2],.01) for h in holes)==1
        edges=[e for e in snapshot['circular_edges'] if near(e[0],x,.01) and near(e[1],y,.01) and near(e[3],part['hole_diameter_mm']/2,.01)]
        checks[f'through_{i}']=len(edges)==2 and all(any(near(e[2],z,.01) and e[4] for e in edges) for z in (0,size[2]))
    return {'status':'pass' if all(checks.values()) else 'fail','checks':checks}


def dof_valid(row, moving):
    """@brief 私有 SDK 输出只作一致性诊断；自由度由真实 Mate、约束状态和六轴求解探针联合证明。"""
    if row.get('status')!='pass' or row.get('errors') or type(row.get('return_code')) is not int: return False
    if any(row.get(k,{}).get('state')!=0 for k in ('rotation_axis_1','rotation_axis_2')): return False
    for name in ('translation_axis_1','translation_axis_2'):
        axis=row.get(name,{})
        if type(axis.get('state')) is not int: return False
        vector=axis.get('vector')
        if vector is not None and (not moving or len(vector)!=3 or not all(near(a,b,1e-6) for a,b in zip(map(abs,vector),(0,0,1)))): return False
    return True


def validate_neutral(snapshot,spec):
    """@brief 对原生及 STEP 实体逐体匹配体积、世界质心和真实圆柱面轴线/面积。"""
    bodies=snapshot['bodies']
    checks={'nine_bodies':len(bodies)==9,'no_errors':snapshot.get('errors')==[]}
    used=set()
    for role,instance in spec['instances'].items():
        part=spec['parts'][instance['part']]
        volume,center=part_properties(part)
        center=[a+b for a,b in zip(center,instance['translation_mm'])]
        matches=[i for i,b in enumerate(bodies) if i not in used and near(b['volume_mm3'],volume,.1)
                 and all(near(a,c,.02) for a,c in zip(b['center_mm'],center))]
        valid=len(matches)==1
        if valid:
            index=matches[0]; used.add(index); body=bodies[index]
            target=part.get('hole_diameter_mm',part.get('diameter_mm'))
            cylinders=body['cylinders']
            groups={}
            for cyl in cylinders:
                key=tuple(round(v,4) for v in [cyl['diameter_mm'],*cyl['origin_mm'][:2]])
                groups.setdefault(key,[]).append(cyl)
            positions=part.get('hole_centers_mm',[[0,0]] if 'diameter_mm' in part else [])
            valid=len(groups)==len(positions)
            for x,y in positions:
                candidates=[g for key,g in groups.items() if near(key[0],target,.01)
                    and near(key[1],x+instance['translation_mm'][0],.01) and near(key[2],y+instance['translation_mm'][1],.01)]
                valid &= len(candidates)==1
                if len(candidates)==1:
                    group=candidates[0]
                    length=part.get('size_mm',[0,0,part.get('length_mm')])[2]
                    valid &= near(sum(c['area_mm2'] for c in group),math.pi*target*length,.05)
                    valid &= all(c['internal']==bool(part.get('hole_centers_mm')) and near(abs(c['axis'][2]),1,1e-6) for c in group)
        checks[role]=bool(valid)
    return {'status':'pass' if all(checks.values()) else 'fail','checks':checks}


def validate_native(snapshot,spec):
    """@brief 组件、原生 Mate、预期约束与空间几何联合检查；零干涉本身不充分。"""
    components=snapshot['components']
    checks={'component_count':len(components)==9,'unique_ids':len({c['id'] for c in components})==9 and all(type(c['id']) is int and c['id']>0 for c in components),
        'unique_roles':{c['role'] for c in components}==set(spec['instances']), 'rebuild':snapshot.get('rebuild') is True,
        'geometry':validate_neutral(snapshot['neutral'],spec)['status']=='pass',
        'native_interference':snapshot['interference'].get('status')=='pass' and snapshot['interference'].get('interference_count')==0
            and snapshot['interference'].get('items')==[] and snapshot['interference'].get('errors')==[],
        'references':snapshot.get('reference_files')==sorted({c['path'] for c in components}) and snapshot.get('reference_errors')==[]}
    for c in components:
        instance=spec['instances'].get(c['role'])
        if instance is None: continue
        transform=c['transform']
        moving=c['role'].startswith(('slider_','finger_'))
        checks[c['role']]= c['file_exists'] is True and Path(c['path']).name==instance['part']+'.SLDPRT' and c['suppression'] in (2,3) \
            and c['fixed'] is instance['fixed'] and c['constrained_status']==(2 if moving else 3) \
            and len(transform)==16 and all(near(a,b,.00002) for a,b in zip(transform[9:12],[v/1000 for v in instance['translation_mm']])) \
            and all(near(a,b,1e-6) for a,b in zip(transform[:9],[1,0,0,0,1,0,0,0,1])) and near(transform[12],1,1e-6) \
            and dof_valid(c['dofs'],moving)
    mate_evidence=snapshot['mates']
    mates=mate_evidence.get('mates',[])
    contract=mate_contract()
    checks['mate_inventory']=mate_evidence.get('status')=='pass' and mate_evidence.get('errors')==[] and len(mates)==len(contract) \
        and {m['name'] for m in mates}=={m['name'] for m in contract}
    by_name={m['name']:m for m in mates}
    ids={c['role']:c['id'] for c in components}
    for wanted in contract:
        actual=by_name.get(wanted['name'],{})
        entities=actual.get('entities',[])
        checks[wanted['name']+'_mate']=actual.get('type')==wanted['type'] and actual.get('suppressed') is False \
            and actual.get('health',{}).get('health_status')=='healthy' and len(entities)==2 \
            and {e['role'] for e in entities}==set(wanted['roles']) \
            and all(e.get('reference_present') is True and bool(e.get('entity_params')) and e.get('component_id')==ids.get(e['role']) for e in entities) \
            and (wanted['type']!=1 or actual.get('lock_rotation') is wanted['lock_rotation'])
    return {'status':'pass' if all(checks.values()) else 'fail','checks':checks}


def workflow_checks(report):
    """@brief 每次验收从实际快照重算，不相信缓存 PASS。"""
    spec=load_spec(); checks={}
    parts=report.get('parts',{})
    checks['five_native_parts']=set(parts)==set(spec['parts'])
    for key,part in spec['parts'].items():
        checks['part_'+key]=all(validate_part(parts[key][stage],part,spec)['status']=='pass' for stage in ('built','reopened'))
    for stage in ('built','reopened'):
        checks[stage]=validate_native(report[stage],spec)['status']=='pass'
        checks[stage+'_bom']=bom_valid(report[stage]['bom'],spec)
    checks['persisted_identity']={c['role']:(c['id'],c['name'],c['path']) for c in report['built']['components']}=={c['role']:(c['id'],c['name'],c['path']) for c in report['reopened']['components']}
    checks['step_reopened']=validate_neutral(report['step_reopened'],spec)['status']=='pass'
    probes=report.get('motion_probes',[])
    checks['independent_motion']=motion_evidence_valid(probes,spec)
    controls=report.get('native_fault_controls',{})
    collision=controls.get('interference',{})
    interference=collision.get('snapshot',{}).get('interference',{})
    suppressed=controls.get('suppressed_mate',{})
    checks['native_fault_detection']=(collision.get('solver_success') is True and collision.get('restored') is True
        and interference.get('status')=='warn' and (interference.get('interference_count') or 0)>0
        and len(interference.get('items',[]))==interference.get('interference_count')
        and not interference.get('errors') and all(i.get('volume_mm3',0)>0 for i in interference.get('items',[]))
        and validate_native(collision['snapshot'],spec)['status']=='fail'
        and suppressed.get('restored') is True and validate_native(suppressed['snapshot'],spec)['status']=='fail'
        and any(m['name']=='slider_left_guide' and m['suppressed'] is True for m in suppressed['snapshot']['mates']['mates']))
    review=report.get('review',{})
    evaluation=review.get('evaluation',{})
    applicable=('rebuild_succeeded','model_available','previews_created','previews_not_blank','expected_outputs_exist','feature_summary_available','feature_errors_absent')
    checks['original_review']=evaluation.get('status')=='pass' and evaluation.get('issues')==[] and all(review.get('checks',{}).get(k) is True for k in applicable)
    previews=review.get('previews',[])
    paths={str(Path(report['output_dir'])/'review'/f'{CASE_ID}_{v}.bmp') for v in VIEWS}
    checks['four_real_previews']=len(previews)==4 and {p['path'] for p in previews}==paths and all(p['exists'] and not p['likely_blank'] and p['width']>=640 and p['height']>=480 for p in previews)
    return checks


def bom_valid(rows,spec):
    """@brief BOM 按真实引用文件统计，五种零件数量必须为 1/2/2/2/2。"""
    expected={p:sum(i['part']==p for i in spec['instances'].values()) for p in spec['parts']}
    actual={Path(r['file']).stem:r['quantity'] for r in rows}
    return len(rows)==5 and actual==expected and all(r.get('excluded_from_bom') is False
        and r.get('part_number')=='P0-2-'+Path(r['file']).stem.upper()
        and r.get('material')=='6061-T6' and r.get('description')==part_description(Path(r['file']).stem,spec['parts'][Path(r['file']).stem]) for r in rows)


def part_description(name,part):
    """@brief BOM 的实际工程描述含尺寸、孔位与功能，来自统一规格而非文件大小填充。"""
    functions={'base':'安装底座，为两端导向支撑提供固定基准','rod':'圆柱导向杆，承接滑块的轴向导向',
        'support':'导杆端部支撑座，定位两根平行导杆','slider':'移动滑块，沿双导杆移动并承载夹持手指','finger':'夹持手指，与对应滑块固定连接'}
    return functions[name]+'；名义参数（mm）：'+json.dumps(part,ensure_ascii=False,sort_keys=True)


def motion_evidence_valid(probes,spec):
    """@brief 根据原始位姿重算独立运动与恢复，禁止仅相信缓存的成功标志。"""
    modes=('tz_negative','tz_positive','tx','ty','rx','ry','rz')
    if len(probes)!=14 or {(p.get('role'),p.get('mode')) for p in probes}!={(r,m) for r in ('slider_left','slider_right') for m in modes}: return False
    for p in probes:
        axial=p['mode'].startswith('tz_')
        if (axial and p.get('solver_success') is not True) or p.get('restored') is not True: return False
        requested=probe_target(p['before'][p['role']],p['mode'],spec)
        for key in ('requested','transform_readback'):
            if len(p.get(key,[]))!=16 or not all(near(a,b,1e-10) for a,b in zip(p[key],requested)): return False
        for role,instance in spec['instances'].items():
            initial=[1,0,0,0,1,0,0,0,1,*[v/1000 for v in instance['translation_mm']],1,0,0,0]
            target=list(initial)
            active=role in (p['role'],p['role'].replace('slider','finger'))
            if axial and active: target[11]+=(-1 if p['mode']=='tz_negative' else 1)*spec['motion_probe_mm']/1000
            if not axial and active:
                # 求解器可把被约束方向的请求投影到自由 Z 轴，旋转和横向偏移仍必须完全受限。
                z=p['after'][p['role']][11]
                if not near(z,initial[11],spec['motion_probe_mm']/1000): return False
                target[11]=z
            for key,wanted in (('before',initial),('after',target),('end',initial)):
                actual=p.get(key,{}).get(role,[])
                if len(actual)!=16 or not all(near(a,b,1e-7) for a,b in zip(actual,wanted)): return False
    return True


def probe_target(initial,mode,spec):
    """@brief 生成六轴测试请求；必须回读实际 MathTransform，避免空写入产生假探针。"""
    target=list(initial)
    if mode.startswith('tz_'): target[11]+=(-1 if mode=='tz_negative' else 1)*spec['motion_probe_mm']/1000
    elif mode in ('tx','ty'): target[9 if mode=='tx' else 10]+=.002
    else:
        c,s=math.cos(.05),math.sin(.05)
        target[:9]={'rx':[1,0,0,0,c,-s,0,s,c],'ry':[c,0,s,0,1,0,-s,0,c],'rz':[c,-s,0,s,c,0,0,0,1]}[mode]
    return target


def inspect_delivery(output,report):
    """@brief 额外校验 BOM CSV、规格及全部引用确实位于本轮目录，杜绝串轮路径。"""
    spec=load_spec()
    if json.loads((output/'spec.json').read_text(encoding='utf-8'))!=spec: raise ValueError('SPEC_MISMATCH')
    with (output/'bom.csv').open(encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.DictReader(stream))
    expected=report['reopened']['bom']
    if len(rows)!=len(expected): raise ValueError('BOM_CSV_MISMATCH')
    for row,actual in zip(rows,expected):
        if any(row.get(k)!=str(v) for k,v in actual.items()): raise ValueError('BOM_CSV_MISMATCH')
    for stage in ('built','reopened'):
        for c in report[stage]['components']:
            wanted=(output/'parts'/(spec['instances'][c['role']]['part']+'.SLDPRT')).resolve()
            if Path(c['path']).resolve()!=wanted or not wanted.is_file() or c['file_sha256']!=sha256_file(wanted): raise ValueError('REFERENCE_MISMATCH')


def neutral_snapshot(model):
    """@brief 从真实 Body2 和组件变换读取世界几何，支持 STEP 导入为多体或装配。"""
    from scripts.sw_connect import get_com_member as member
    sources=[]; rows=[]
    if member(model,'GetType')==1:
        sources=[(model,None)]
    else:
        for component in list(member(model,'GetComponents',False) or []):
            part=member(component,'GetModelDoc2')
            if part is None: raise RuntimeError('STEP/装配引用未加载')
            if member(part,'GetType')==1: sources.append((part,list(member(member(component,'GetTotalTransform',False),'ArrayData'))))
    def world(point,transform,vector=False):
        if transform is None: return list(point)
        return [sum(point[j]*transform[j*3+i] for j in range(3))+(0 if vector else transform[9+i]) for i in range(3)]
    for part,transform in sources:
        for body in member(part,'GetBodies2',0,False) or []:
            mass=list(member(body,'GetMassProperties',1.0))
            cylinders=[]
            for face in member(body,'GetFaces'):
                surface=member(face,'GetSurface')
                if member(surface,'IsCylinder'):
                    data=list(member(surface,'CylinderParams'))
                    cylinders.append({'diameter_mm':data[6]*2000,'origin_mm':[v*1000 for v in world(data[:3],transform)],
                        'axis':world(data[3:6],transform,True),'internal':bool(member(face,'FaceInSurfaceSense')),
                        'area_mm2':float(member(face,'GetArea'))*1e6})
            rows.append({'volume_mm3':mass[3]*1e9,'center_mm':[v*1000 for v in world(mass[:3],transform)],'cylinders':cylinders})
    return {'bodies':rows,'errors':[],'source':'SolidWorks Body2 B-Rep + GetMassProperties + native component transform'}


def assembly_snapshot(model):
    """@brief 读取保存前或重开后的真实原生装配、依赖、Mate、DOF、BOM 与干涉。"""
    from scripts.sw_connect import get_com_member as member
    from scripts.sw_assembly import inspect_component_dofs,inspect_native_mates
    from scripts.sw_measure import inspect_interference
    from scripts.sw_delivery import collect_assembly_bom
    rows=[]
    for c in member(model,'GetComponents',True) or []:
        path=Path(member(c,'GetPathName'))
        rows.append({'id':int(member(c,'GetID')),'name':str(member(c,'Name2')),'role':str(member(c,'ComponentReference')),
            'path':str(path),'file_exists':path.is_file(),'file_sha256':sha256_file(path) if path.is_file() else None,
            'fixed':bool(member(c,'IsFixed')),'suppression':int(member(c,'GetSuppression2')),
            'constrained_status':int(member(c,'GetConstrainedStatus')),'transform':list(member(member(c,'Transform2'),'ArrayData')),
            'dofs':inspect_component_dofs(c)})
    dependencies=list(member(model,'GetDependencies2',True,True,False) or [])
    references=sorted(set(str(Path(p)) for p in dependencies if Path(str(p)).is_absolute() and str(p).lower().endswith('.sldprt')))
    return {'rebuild':bool(member(model,'ForceRebuild3',False)),'components':rows,'reference_files':references,
        'reference_errors':[] if all(Path(p).is_file() for p in references) else ['missing_file'],
        'raw_dependencies':dependencies,'mates':inspect_native_mates(model),'interference':inspect_interference(model),
        'bom':collect_assembly_bom(model),'neutral':neutral_snapshot(model)}


def create_parts(session,output,report,spec):
    """@brief 串行创建五个可编辑零件；每件保存、关闭重开并复核几何参数。"""
    from scripts.sw_connect import get_com_member as member
    from scripts.sw_part import sketch,sketch_rectangle,sketch_circle,extrude_boss,extrude_cut
    from scripts.sw_sketch_quality import fully_define_sketch
    from scripts.sw_appearance import set_document_appearance
    from scripts.sw_document_data import set_custom_properties
    config=report['runtime_config']
    database=resolve_material_database(config,report['doctor']['installations']['solidworks']['executable'])
    report['resolved_material_database']={'path':str(database),'sha256':sha256_file(database)}
    parts={}; (output/'parts').mkdir()
    for name,part in spec['parts'].items():
        model=session.new_part(report['resolved_templates']['part']['path'])
        with sketch(model,'Front Plane') as ref:
            if 'diameter_mm' in part: sketch_circle(model,0,0,part['diameter_mm']/2000)
            else: sketch_rectangle(model,*[v/1000 for v in part['center_xy_mm']],*[v/1000 for v in part['size_mm'][:2]])
            fully_define_sketch(model)
        depth=part.get('size_mm',[0,0,part.get('length_mm')])[2]
        boss=extrude_boss(model,ref,depth/1000,direction=False)
        if boss is None: raise RuntimeError('零件母体失败: '+name)
        boss_name=str(member(boss,'Name'))
        if part.get('hole_centers_mm'):
            with sketch(model,'Front Plane') as ref:
                for x,y in part['hole_centers_mm']: sketch_circle(model,x/1000,y/1000,part['hole_diameter_mm']/2000)
                fully_define_sketch(model)
            if extrude_cut(model,ref,0,direction=True) is None: raise RuntimeError('导向孔切除失败')
        member(model,'SetMaterialPropertyName2','',str(database),spec['material_entry'])
        properties=set_custom_properties(model,{'PartNumber':'P0-2-'+name.upper(),
            'Description':part_description(name,part),'Material':'6061-T6'})
        if properties['success'] is not True: raise RuntimeError('零件 BOM 属性回读失败: '+name)
        color={'base':[.3,.4,.6],'rod':[.7,.7,.72],'support':[.3,.4,.6],'slider':[.8,.5,.15],'finger':[.35,.35,.38]}[name]
        set_document_appearance(model,color)
        row={}
        for stage in ('built','reopened'):
            if not member(model,'ForceRebuild3',False): raise RuntimeError('零件重建失败')
            snap=snapshot_model(model,boss_name)
            row[stage]=snap
            row[stage]['validation']=validate_part(snap,part,spec)
            if row[stage]['validation']['status']!='pass':
                report.setdefault('parts',{})[name]=row
                write_json(output/'parts.json',report['parts'])
                raise RuntimeError('零件验证失败: '+name+' '+str(row[stage]['validation']))
            if stage=='built':
                path=output/'parts'/f'{name}.SLDPRT'
                if path.exists() or not session.save(model=model,file_path=str(path)): raise RuntimeError('零件保存失败')
                session.close(model=model)
                model=session.open(str(path),silent=True)
        session.close(model=model)
        parts[name]=row
        report['parts']=parts
        write_json(output/'parts.json',parts)
        print('零件完成: '+name,flush=True)


def create_assembly(session,output,report,spec):
    """@brief 初始放置后创建真实 Mate；不使用变换矩阵替代约束。"""
    from scripts.sw_connect import get_com_member as member, create_empty_dispatch_variant
    from scripts.sw_assembly import (add_component,build_transform_data_x,create_math_transform,get_component_feature_entity,
        select_entities_for_mate,add_mate5_checked,iter_feature_tree,get_assembly_entity)
    model=session.new_assembly(report['resolved_templates']['assembly']['path'])
    components={}
    for role,instance in spec['instances'].items():
        c=add_component(model,str(output/'parts'/f"{instance['part']}.SLDPRT"),sw=session.sw)
        if c is None: raise RuntimeError('插入组件失败: '+role)
        c.ComponentReference=role
        model.ClearSelection2(True)
        if not c.Select4(False,create_empty_dispatch_variant(),False): raise RuntimeError('无法选择组件')
        member(model,'UnfixComponent')
        data=build_transform_data_x(*[v/1000 for v in instance['translation_mm']])
        c.Transform2=create_math_transform(session.sw,data)
        actual=list(member(member(c,'Transform2'),'ArrayData'))
        if not all(near(a,b,1e-9) for a,b in zip(actual,data)): raise RuntimeError('初始放置回读失败: '+role)
        if instance['fixed']:
            model.ClearSelection2(True)
            c.Select4(False,create_empty_dispatch_variant(),False)
            member(model,'FixComponent')
        components[role]=c
    def cylinder(role,x):
        c=components[role]; part=member(c,'GetModelDoc2')
        faces=[]
        for body in member(part,'GetBodies2',0,False):
            for face in member(body,'GetFaces'):
                surface=member(face,'GetSurface')
                if member(surface,'IsCylinder'):
                    params=list(member(surface,'CylinderParams'))
                    if near(params[0]*1000,x,.01): faces.append(face)
        if len(faces)!=1: raise RuntimeError('圆柱面不唯一: '+role)
        return get_assembly_entity(c,faces[0])
    for wanted in mate_contract():
        left,right=wanted['roles']
        if wanted['type']==1:
            entities=[cylinder(left,wanted['left_cylinder_x_mm']),cylinder(right,wanted['right_cylinder_x_mm'])]
        else: entities=[get_component_feature_entity(components[r],wanted['plane']) for r in (left,right)]
        before={str(member(f,'Name')) for f,_ in iter_feature_tree(model)}
        select_entities_for_mate(model,*entities)
        mate=add_mate5_checked(model,wanted['type'],distance=wanted.get('distance_mm',0)/1000,
            flip=wanted.get('flip',False),lock_rotation=wanted.get('lock_rotation',False))
        if mate is None: raise RuntimeError('Mate 返回空')
        added=[f for f,_ in iter_feature_tree(model) if str(member(f,'Name')) not in before and str(member(f,'GetTypeName2')).startswith('Mate') and member(f,'GetTypeName2')!='MateGroup']
        if len(added)!=1: raise RuntimeError('无法唯一关联新 Mate 特征')
        added[0].Name=wanted['name']
        print('Mate: '+wanted['name'],flush=True)
    return model,components


def motion_probe(model,components,spec,sw):
    """@brief 原生求解器六轴探针：只允许 Z 平移，另外五个方向必须约束并逐次恢复。"""
    from scripts.sw_connect import get_com_member as member
    from scripts.sw_assembly import create_math_transform
    result=[]
    read=lambda:{role:list(member(member(c,'Transform2'),'ArrayData')) for role,c in components.items()}
    for role in ('slider_left','slider_right'):
        for mode in ('tz_negative','tz_positive','tx','ty','rx','ry','rz'):
            before=read(); c=components[role]
            desired=probe_target(before[role],mode,spec)
            transform=create_math_transform(sw,desired)
            requested_readback=list(member(transform,'ArrayData'))
            success=bool(member(c,'SetTransformAndSolve2',transform))
            member(model,'ForceRebuild3',False)
            after=read()
            transform=create_math_transform(sw,before[role])
            restored=bool(member(c,'SetTransformAndSolve2',transform))
            member(model,'ForceRebuild3',False)
            end=read()
            restored &= all(all(near(a,b,1e-7) for a,b in zip(end[k],before[k])) for k in before)
            result.append({'role':role,'mode':mode,'before':before,'requested':desired,'transform_readback':requested_readback,'after':after,'end':end,
                'solver_success':success,'restored':bool(restored)})
    return result


def native_fault_controls(model,components,sw):
    """@brief 在自有装配中注入实际穿透与 Mate 抑制，保留原始证据，恢复后再保存交付。"""
    import pythoncom
    from win32com.client import VARIANT
    from scripts.sw_connect import get_com_member as member
    from scripts.sw_assembly import iter_feature_tree,create_math_transform
    c=components['slider_left']
    original=list(member(member(c,'Transform2'),'ArrayData'))
    desired=list(original); desired[11]=.025
    transform=create_math_transform(sw,desired)
    success=bool(member(c,'SetTransformAndSolve2',transform))
    member(model,'ForceRebuild3',False)
    collision={'solver_success':success,'snapshot':assembly_snapshot(model)}
    transform=create_math_transform(sw,original)
    restored=bool(member(c,'SetTransformAndSolve2',transform))
    member(model,'ForceRebuild3',False)
    collision['restored']=restored and all(near(a,b,1e-7) for a,b in zip(member(member(c,'Transform2'),'ArrayData'),original))
    features=[f for f,_ in iter_feature_tree(model) if member(f,'Name')=='slider_left_guide']
    if len(features)!=1: raise RuntimeError('故障注入的 Mate 不能唯一定位')
    feature=features[0]
    empty=VARIANT(pythoncom.VT_EMPTY,None)
    if not feature.SetSuppression2(0,1,empty): raise RuntimeError('原生 Mate 抑制失败')
    try:
        member(model,'ForceRebuild3',False)
        suppressed={'snapshot':assembly_snapshot(model)}
    finally:
        if not feature.SetSuppression2(1,1,empty): raise RuntimeError('Mate 恢复失败')
        member(model,'ForceRebuild3',False)
    suppressed['restored']=member(feature,'IsSuppressed') is False
    return {'interference':collision,'suppressed_mate':suppressed}


def run_cad(output,report):
    """@brief 独占真实实例，串行执行零件/装配/回读/导出；只清理本轮可证明拥有的文档。"""
    import pythoncom
    from scripts.sw_session import SolidWorksSession
    from scripts.sw_connect import get_com_member as member,get_sw_version,find_template
    from scripts.sw_process import solidworks_processes
    from scripts.sw_part import clear_sketch_selection_cache
    from scripts.sw_review import run_review
    from scripts.sw_delivery import export_assembly_bom_csv
    from apps.desktop.cad_workbench.engineering_orchestrator import build_engineering_plan
    spec=load_spec(); write_json(output/'spec.json',spec)
    plan=build_engineering_plan('双指夹爪：参数化零件、导向孔、原生装配 Mate、BOM 与 STEP 交付；不执行 Motion、驱动或制造放行').to_dict()
    plan['execution_scope']={'implemented':['part-modeling','assembly-mates','BOM','STEP','review'],
        'excluded':['Motion Study','动力传递','制造工程图','载荷与公差链'],'core_scope':'terminal_adaptation_only'}
    write_json(output/'engineering_plan.json',plan)
    prior=solidworks_processes(); report['processes_before']=prior
    if prior is None or prior: raise RuntimeError('BLOCKED: 已有或未知 SolidWorks 进程，保留用户实例')
    session=None; model=None; components={}; pid=None; owned=False
    pythoncom.CoInitialize()
    try:
        session=SolidWorksSession(version=2026,wait_seconds=report['runtime_config']['connect_timeout_seconds'],visible=True,max_documents=12)
        report.update(connection=session.connection_info,version=get_sw_version(session.sw))
        pid=int(member(session.sw,'GetProcessID')); report['process_created_at']=process_created_at(pid)
        owned=session.connection_info.get('started_by_cad_studio') is True and pid not in prior
        if not owned or report['version']['year']!=2026 or list(member(session.sw,'GetDocuments') or []): raise RuntimeError('BLOCKED: 实例所有权不能证明')
        report['resolved_templates']={}
        for kind in ('part','assembly'):
            template=report['runtime_config'][kind+'_template'] or find_template(session.sw,kind)
            if not template or not Path(template).is_file(): raise RuntimeError('BLOCKED: '+kind+' 模板不可用')
            report['resolved_templates'][kind]={'path':str(template),'sha256':sha256_file(Path(template))}
        create_parts(session,output,report,spec)
        model,components=create_assembly(session,output,report,spec)
        report['motion_probes']=motion_probe(model,components,spec,session.sw)
        report['native_fault_controls']=native_fault_controls(model,components,session.sw)
        write_json(output/'native_fault_controls.json',report['native_fault_controls'])
        report['built']=assembly_snapshot(model); write_json(output/'built.json',report['built'])
        native=output/f'{CASE_ID}.SLDASM'
        if native.exists() or not session.save(model=model,file_path=str(native)): raise RuntimeError('装配保存失败')
        # 显式关掉本轮拥有的装配及自动加载的引用，然后从磁盘重新打开。
        close_test_documents(session,output)
        model=None; components={}; gc.collect()
        model=session.open(str(native),silent=True)
        report['reopened']=assembly_snapshot(model); write_json(output/'reopened.json',report['reopened'])
        report['bom_export']=export_assembly_bom_csv(model,output/'bom.csv')
        step=output/f'{CASE_ID}.step'
        if step.exists() or not session.export(model=model,output_path=str(step)): raise RuntimeError('STEP 导出失败')
        if not step.read_text(encoding='ascii',errors='replace').lstrip().startswith('ISO-10303-21;'): raise RuntimeError('STEP 文件头错误')
        report['review'],report['review_path']=run_review(model,output/'review',basename=CASE_ID,views=VIEWS,
            expected_outputs=[str(native),str(step),str(output/'bom.csv')])
        close_test_documents(session,output); model=None; gc.collect()
        model=session.open(str(step),silent=True)
        report['step_reopened']=neutral_snapshot(model); write_json(output/'step_reopened.json',report['step_reopened'])
    finally:
        cleanup={'status':'failed','owned_pid':pid,'errors':[]}; report['cleanup']=cleanup
        try:
            if session is not None and owned:
                close_test_documents(session,output)
                cleanup['remaining_documents']=len(list(member(session.sw,'GetDocuments') or []))
                if cleanup['remaining_documents']==0: cleanup['exit_requested']=session.quit_owned_instance()
        except Exception: cleanup['errors'].append(traceback.format_exc())
        model=None; components={}; session=None; clear_sketch_selection_cache(); gc.collect(); pythoncom.CoUninitialize()
        deadline=time.monotonic()+report['runtime_config']['cleanup_timeout_seconds']
        while pid and time.monotonic()<deadline:
            processes=solidworks_processes()
            if processes is None or pid not in processes: break
            time.sleep(.25)
        cleanup['processes_after']=solidworks_processes()
        if cleanup.get('remaining_documents')==0 and cleanup.get('exit_requested') is True and cleanup['processes_after']==prior and not cleanup['errors']: cleanup['status']='pass'


def close_test_documents(session,output):
    """@brief 优先复用 Session，自动加载的引用也仅按本轮路径清理，拒绝关闭未知文档。"""
    from scripts.sw_connect import get_com_member as member
    from scripts.sw_part import clear_sketch_selection_cache
    session.close_owned_documents()
    for doc in list(member(session.sw,'GetDocuments') or []):
        path=str(member(doc,'GetPathName') or '')
        if path and Path(path).resolve().is_relative_to(output):
            clear_sketch_selection_cache(doc)
            session.sw.CloseDoc(str(member(doc,'GetTitle')))


def main(argv=None):
    """@brief 注册入口分派至共用严谨验收壳。"""
    return harness.main(sys.modules[__name__],argv)


if __name__=='__main__':
    raise SystemExit(main())
