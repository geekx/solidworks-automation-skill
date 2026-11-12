"""@brief P0-2.1：真实夹爪机械保持及九组合行程；不含同步、动力或制造放行。"""
from __future__ import annotations
import csv
import gc
import itertools
import json
import math
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'tests'))
import solidworks_golden_parallel_gripper_2026 as previous
import golden_workflow_harness as harness
from golden_workflow_support import VIEWS,process_created_at,resolve_material_database
from solidworks_golden_mounting_bracket_2026 import snapshot_model,write_json,sha256_file,close_enough as near
from evals.runner import load_golden_workflows

CASE_ID='parallel_gripper_retention_travel'
SNAPSHOT_KEYS=('parts','built','reopened','step_reopened','travel_report','tolerance_report','native_fault_controls')


def load_spec():
    """@brief 唯一规格来源；运行配置不能覆盖尺寸、公差或人工门禁。"""
    return next(w['real_acceptance_2026'] for w in load_golden_workflows()['workflows'] if w['id']==CASE_ID)


def transform(instance,z=None):
    """@brief 初始位姿仅作插入和求解请求，不能代替真实 Mate。"""
    xyz=list(instance['translation_mm'])
    if z is not None: xyz[2]=z
    angle=instance.get('angle_x',0); c,s=math.cos(angle),math.sin(angle)
    return [1,0,0,0,c,-s,0,s,c,*[v/1000 for v in xyz],1,0,0,0]


def world(point,t,vector=False):
    """@brief 使用原生变换数组将局部几何映射到装配坐标。"""
    return [sum(point[j]*t[j*3+i] for j in range(3))+(0 if vector else t[9+i]*1000) for i in range(3)]


def part_contract(name,spec=None):
    """@brief 对互不相交的孔、凸台计算名义体积/质心和圆柱面积，供真实 B-Rep 回读比较。"""
    s=spec or load_spec(); p=s['parts'][name]; signed=[]; cylinders=[]
    def cylinder(d,length,center,axis,internal):
        cylinders.append({'diameter_mm':d,'origin_mm':center,'axis':axis,'internal':internal,'area_mm2':math.pi*d*length})
    if 'diameter' in p:
        d,L=p['diameter'],p['length']; signed.append((math.pi*d*d*L/4,[0,0,L/2]))
        cylinder(d,L,[0,0,0],[0,0,1],False)
        if 'head_diameter' in p:
            h,D=p['head_length'],p['head_diameter']
            for z in (-h/2,L+h/2): signed.append((math.pi*D*D*h/4,[0,0,z]))
            cylinder(D,2*h,[0,0,0],[0,0,1],False)
        size=[p.get('head_diameter',d)]*2+[L+2*p.get('head_length',0)]
    else:
        for x,y,z,cx,cy,start in p['bosses']: signed.append((x*y*z,[cx,cy,start+z/2]))
        size=list(p['size'])
        if name=='base': size[1]=48
        for key,d,centers,axis in (
            ('guide',s['guide_hole_diameter_mm'],[[x,0,p.get('guide_z',0)+p.get('guide',0)/2] for x in s['guide_centers_mm']],[0,0,1]),
            ('tie',s['tie_hole_diameter_mm'],[[x,y,p.get('tie',0)/2] for x,y in s['tie_centers_mm']],[0,0,1]),
            ('finger_holes',s['finger_hole_diameter_mm'],[[x,p['center'][1],z] for x,z in s['finger_centers_mm']],[0,1,0])):
            if key in p:
                for center in centers:
                    signed.append((-math.pi*d*d*p[key]/4,center)); cylinder(d,p[key],center,axis,True)
        if p.get('mount'):
            for x,z in s['mount_centers_mm']:
                d=s['mount_hole_diameter_mm']; center=[x,-32,z]
                signed.append((-math.pi*d*d*12/4,center)); cylinder(d,12,center,[0,1,0],True)
    volume=sum(v for v,_ in signed)
    center=[sum(v*c[i] for v,c in signed)/volume for i in range(3)]
    return {'volume_mm3':volume,'center_mm':center,'cylinders':cylinders,'size_mm':size}


def cylinder_groups(cylinders):
    """@brief 归并 STEP 接缝，仅按同轴/直径归并面积；不丢弃未知圆柱。"""
    groups={}
    for c in cylinders:
        axis=c['axis']; main=max(range(3),key=lambda i:abs(axis[i]))
        if not all(near(abs(axis[i]),1 if i==main else 0,1e-6) for i in range(3)): raise ValueError('未知圆柱轴')
        key=(main,round(c['diameter_mm'],4),*[round(c['origin_mm'][i],4) for i in range(3) if i!=main],bool(c['internal']))
        groups[key]=groups.get(key,0)+c['area_mm2']
    return groups


def geometry_matches(actual,expected):
    """@brief 逐体匹配体积、质心、所有圆柱孔径/轴线/面积；缺失或未知失败。"""
    if not near(actual.get('volume_mm3'),expected['volume_mm3'],.1): return False
    if len(actual.get('center_mm',[]))!=3 or not all(near(a,b,.01) for a,b in zip(actual['center_mm'],expected['center_mm'])): return False
    a,b=cylinder_groups(actual['cylinders']),cylinder_groups(expected['cylinders'])
    return a.keys()==b.keys() and all(near(a[k],b[k],.05) for k in b)


def validate_part(snap,name,spec=None):
    """@brief 验证原生参数、完全约束草图、特征树、材料、体积与功能孔。"""
    s=spec or load_spec(); p=s['parts'][name]; contract=part_contract(name,s)
    boss_count=len(p.get('bosses',[])) or (3 if 'head_diameter' in p else 1)
    cuts=sum(k in p for k in ('guide','tie','finger_holes','mount'))
    types=[f.get('underlying_type',f['type']) for f in snap['features']['features']]
    checks={'single_native_solid':snap['body_count']==1 and len(snap['neutral']['bodies'])==1,
        'geometry':len(snap['neutral']['bodies'])==1 and geometry_matches(snap['neutral']['bodies'][0],contract),
        'dimensions':all(near(snap['geometry'].get('envelope_mm',{}).get(k),v,.01) for k,v in zip(('length','width','height'),contract['size_mm'])),
        'editable_depth':near(snap['thickness_mm'],p.get('length',p.get('size',[0,0,0])[2]),.01),
        'material':snap['material']['name']==p['material'] and bool(snap['material']['database']),
        'mass_readable':not snap['mass'].get('errors') and near(snap['mass'].get('density_kg_m3'),p['density_kg_m3'],1),
        'sketches':snap['sketch_quality']['status']=='pass' and len(snap['sketch_quality']['sketches'])==boss_count+cuts,
        'features':sum(t in ('Boss','Extrusion') for t in types)==boss_count and types.count('Cut')==cuts,
        'health':not any(snap['features'].get(k) for k in ('feature_error','faulty_features','warning_features','unknown_feature_health'))}
    if name=='base':
        checks['physical_center_stop']=all(any(q['axis']==2 and near(q['coordinate_mm'],z,.01) for q in snap['planes']) for z in (54,62))
    return {'status':'pass' if all(checks.values()) else 'fail','checks':checks}


def description(name,spec):
    """@brief BOM 使用实际规格；自定义铆钉不是标准件或预紧证明。"""
    return 'P0-2.1 保持与限位结构；'+name+'；mm；'+json.dumps(spec['parts'][name],ensure_ascii=False,sort_keys=True)


def mate_contract(spec=None):
    """@brief 固定件用三基准 Mate，滑块保留双导杆同心及真实距离上下限。"""
    s=spec or load_spec(); rows=[]
    for role,i in s['instances'].items():
        if role=='base' or role.startswith('slider'): continue
        other=i.get('parent', 'slider_'+role.split('_')[1] if role.startswith('finger_') and not role.startswith('finger_pin') else 'base')
        relative=[a-b for a,b in zip(i['translation_mm'],s['instances'][other]['translation_mm'])]
        if role.startswith('rod_'):
            rows.append({'name':role+'_guide','roles':[role,'support_left'],'type':1,'left_x':0,'right_x':i['translation_mm'][0]})
            planes=[('Front Plane','Front Plane',relative[2]),('Top Plane','Top Plane',0)]
        elif i.get('angle_x'):
            planes=[('Right Plane','Right Plane',relative[0]),('Front Plane','Top Plane',relative[1]),('Top Plane','Front Plane',relative[2])]
        else: planes=[(p,p,v) for p,v in zip(('Right Plane','Top Plane','Front Plane'),relative)]
        for index,(left,right,distance) in enumerate(planes):
            rows.append({'name':role+'_datum_'+str(index),'roles':[role,other],'type':5 if distance else (3 if role.startswith('rod_') else 0),
                'planes':[left,right],'distance_mm':abs(distance),
                # 铆钉绕 X 转 90° 后，Top 法向与滑块 Front 相反；沿反向法向修正距离符号。
                'flip':bool(distance>0) ^ bool(i.get('angle_x') and left=='Top Plane')})
    for role in ('slider_left','slider_right'):
        for rod,x in [('rod_left',-18),('rod_right',18)]: rows.append({'name':role+'_'+rod,'roles':[role,rod],'type':1,'left_x':x,'right_x':0})
        low,mid,high=s['travel_mm'][role]
        reference='endplate_left' if role=='slider_left' else 'base'
        origin=s['instances'][reference]['translation_mm'][2]
        rows.append({'name':role+'_travel_limit','roles':[role,reference],'type':5,'planes':['Front Plane','Front Plane'],
            'distance_mm':mid-origin,'minimum_mm':low-origin,'maximum_mm':high-origin,'flip':True})
    return rows


def positions_for(left,right,spec=None):
    """@brief 两组独立移动，手指及其真实连接件必须随对应滑块移动。"""
    s=spec or load_spec(); result={}
    for role,i in s['instances'].items():
        side='slider_left' if role in ('slider_left','finger_left') or i.get('parent')=='slider_left' else 'slider_right' if role in ('slider_right','finger_right') or i.get('parent')=='slider_right' else None
        z=i['translation_mm'][2]
        if side: z+=(left if side=='slider_left' else right)-s['instances'][side]['translation_mm'][2]
        result[role]=transform(i,z)
    return result


def expected_neutral(spec=None):
    """@brief STEP 与原生装配共享同一名义几何合同，不接受仅实体数量一致。"""
    s=spec or load_spec(); result=[]
    for role,i in s['instances'].items():
        body=part_contract(i['part'],s); t=transform(i)
        body['center_mm']=world(body['center_mm'],t)
        for c in body['cylinders']:
            c['origin_mm']=world(c['origin_mm'],t); c['axis']=world(c['axis'],t,True)
        result.append(body)
    return result


def neutral_valid(snapshot,spec=None):
    """@brief 逐实体唯一匹配原生零件参数几何，STEP 缺件/错位/错孔均失败。"""
    targets=expected_neutral(spec); bodies=snapshot['bodies']; used=set()
    if snapshot.get('errors')!=[] or len(bodies)!=len(targets): return False
    for target in targets:
        matches=[i for i,b in enumerate(bodies) if i not in used and geometry_matches(b,target)]
        if len(matches)!=1: return False
        used.add(matches[0])
    return True


def bom_valid(rows,spec=None):
    """@brief 八种真实零件及各实例数量与 BOM 原生属性一致。"""
    s=spec or load_spec(); wanted={p:sum(i['part']==p for i in s['instances'].values()) for p in s['parts']}
    return len(rows)==len(wanted) and {Path(r['file']).stem:r['quantity'] for r in rows}==wanted and all(
        r['excluded_from_bom'] is False and r['part_number']=='P021-'+Path(r['file']).stem.upper()
        and r['material']==s['parts'][Path(r['file']).stem]['material'] and r['description']==description(Path(r['file']).stem,s) for r in rows)


def native_valid(snapshot,positions=None,spec=None,interference_required=True):
    """@brief 位姿、关联实体、限位特征与实际计算交叉验证；不依赖缓存 PASS。"""
    s=spec or load_spec(); poses=positions or positions_for(-40,22,s); components=snapshot['components']; mates=snapshot['mates']
    checks={'rebuild':snapshot.get('rebuild') is True,'components':len(components)==len(poses) and {c['role'] for c in components}==set(poses),
        'unique_ids':len({c['id'] for c in components})==len(poses),
        'references':snapshot['reference_errors']==[] and snapshot['reference_files']==sorted({c['path'] for c in components})}
    ids={c['role']:c['id'] for c in components}
    for c in components:
        role=c['role']; wanted=poses.get(role,[])
        checks[role]=len(c['transform'])==len(wanted)==16 and all(near(a,b,1e-7) for a,b in zip(c['transform'],wanted)) \
            and c['file_exists'] is True and c['fixed'] is s['instances'][role]['fixed'] and c['suppression'] in (2,3) \
            and type(c['id']) is int and c['id']>0 and c['constrained_status'] in ((2,3) if role.startswith(('slider_','finger_')) else (3,))
    expected=mate_contract(s); by_name={m['name']:m for m in mates['mates']}
    checks['mate_inventory']=mates['status']=='pass' and mates['errors']==[] and len(mates['mates'])==len(expected) and set(by_name)=={m['name'] for m in expected}
    for wanted in expected:
        actual=by_name.get(wanted['name'],{}); entities=actual.get('entities',[])
        checks[wanted['name']]=actual.get('type')==wanted['type'] and actual.get('suppressed') is False and actual.get('health',{}).get('health_status')=='healthy' \
            and len(entities)==2 and {e['role'] for e in entities}==set(wanted['roles']) and all(
                e.get('reference_present') is True and e.get('component_id')==ids.get(e['role']) and bool(e.get('entity_params')) for e in entities)
        if 'minimum_mm' in wanted:
            limit=snapshot['limits'].get(wanted['name'],{})
            distance=(poses[wanted['roles'][0]][11]-poses[wanted['roles'][1]][11])*1000
            checks[wanted['name']+'_bounds']=limit.get('advanced') is True and near(limit.get('minimum_mm'),wanted['minimum_mm'],.001) and near(limit.get('maximum_mm'),wanted['maximum_mm'],.001) and near(limit.get('distance_mm'),distance,.001)
    if interference_required:
        i=snapshot['interference']; checks['native_interference']=i.get('status')=='pass' and i.get('interference_count')==0 and i.get('items')==[] and i.get('errors')==[]
    return {'status':'pass' if all(checks.values()) else 'fail','checks':checks}


def tolerance_contract(spec=None):
    """@brief 表内公差可追溯；其它公差明确待确认，不能冒充制造标准验收。"""
    s=spec or load_spec(); f=s['fit']; shaft=f['shaft_limits_mm']; hole=f['hole_limits_mm']
    clearance=[hole[0]-shaft[1],hole[1]-shaft[0]]
    return {'fit':f,'modeled_shaft_mm':s['parts']['rod']['diameter'],'modeled_hole_mm':s['guide_hole_diameter_mm'],
        'diametral_clearance_limits_mm':clearance,'modeled_diametral_clearance_mm':s['guide_hole_diameter_mm']-s['parts']['rod']['diameter'],
        'status':'proposal_requires_engineer_review','manufacturing_release':False}


def travel_valid(rows,spec=None):
    """@brief 九组合工况必须全部有原生计算、健康 Mate、真实止挡接触和导杆覆盖。"""
    s=spec or load_spec(); combinations=set(itertools.product(s['travel_mm']['slider_left'],s['travel_mm']['slider_right']))
    if len(rows)!=9 or {(r['left_mm'],r['right_mm']) for r in rows}!=combinations: return False
    for row in rows:
        left,right=row['left_mm'],row['right_mm']; snap=row['snapshot']; poses=positions_for(left,right,s)
        if row.get('solver_success') is not True or native_valid(snap,poses,s)['status']!='pass': return False
        requests=row.get('requests',[])
        if len(requests)!=2 or {r['role'] for r in requests}!={'slider_left','slider_right'}: return False
        for request in requests:
            target=left if request['role']=='slider_left' else right
            if request['requested_mm']!=target or not request_valid(request) or not all(near(a,b,1e-7) for a,b in zip(request['actual_transform'],poses[request['role']])): return False
        if not (-58<=left<=-22 and 4<=right<=40 and -69.9<=left and right+18<=69.9): return False
        contact=snap['coincidence']; names={c['name']:c['role'] for c in snap['components']}; pairs=set()
        if contact.get('status') not in ('pass','warn') or contact.get('errors')!=[] or contact.get('interference_count')!=len(contact.get('items',[])) or contact.get('options',{}).get('treat_coincidence_as_interference') is not True: return False
        for item in contact['items']:
            if not near(item.get('volume_mm3'),0,1e-6): return False
            pairs.add(frozenset(names[c['name']] for c in item['components']))
        needed=[('base','support_left'),('base','support_right'),('support_left','endplate_left'),('support_right','endplate_right')]
        needed.extend((end,tie) for end in ('endplate_left','endplate_right') for tie in ('tie_left','tie_right'))
        for side in ('left','right'):
            needed.append(('slider_'+side,'finger_'+side))
            needed.extend((component+'_'+side,'finger_pin_'+side+'_'+suffix) for component in ('slider','finger') for suffix in ('a','b'))
        if left==-58: needed.append(('slider_left','support_left'))
        if left==-22: needed.append(('slider_left','base'))
        if right==4: needed.append(('slider_right','base'))
        if right==40: needed.append(('slider_right','support_right'))
        if any(frozenset(p) not in pairs for p in needed): return False
    return True


def request_valid(row):
    """@brief 请求自身必须真实编组、回读且被求解器执行，不能以缓存的成功标记替代。"""
    wanted=row.get('requested_transform',[]); actual=row.get('request_readback',[])
    update=row.get('mate_update',{})
    return row.get('solver_success') is True and len(wanted)==len(actual)==len(row.get('actual_transform',[]))==16 \
        and near(wanted[11]*1000,row.get('requested_mm'),1e-8) and all(near(a,b,1e-10) for a,b in zip(wanted,actual)) \
        and row.get('positioning_method')=='native_limit_mate_current_distance' and update.get('modified') is True \
        and update.get('advanced') is True and near(update.get('distance_mm'),row['requested_mm']-update['reference_origin_mm'],.001)


def overtravel_valid(rows,spec=None):
    """@brief 四个未提交 Mate 参数的变换请求须保持合法位姿；不作为机械接触阻挡证明。"""
    s=spec or load_spec(); expected={(role,target) for role,b in s['travel_mm'].items() for target in (b[0]-2,b[-1]+2)}
    if len(rows)!=4 or {(r['role'],r['requested_mm']) for r in rows}!=expected: return False
    for row in rows:
        # 越限请求允许求解器返回 False，但请求的编组必须真实；实际位置必须保持在界内。
        wanted=row.get('requested_transform',[]); readback=row.get('request_readback',[]); actual=row.get('actual_transform',[])
        if row.get('positioning_method')!='native_transform_request_no_parameter_commit': return False
        if len(wanted)!=len(readback) or len(wanted)!=16 or len(actual)!=16 or not all(near(a,b,1e-10) for a,b in zip(wanted,readback)) or not near(wanted[11]*1000,row['requested_mm'],1e-8): return False
        z=actual[11]*1000; low,mid,high=s['travel_mm'][row['role']]
        if not low-.001<=z<=high+.001 or near(z,row['requested_mm'],.001) or not near(z,row.get('actual_mm'),1e-8): return False
        poses=positions_for(z if row['role']=='slider_left' else -40,z if row['role']=='slider_right' else 22,s)
        if native_valid(row['snapshot'],poses,s)['status']!='pass': return False
    return True


def physical_overtravel_valid(rows,spec=None):
    """@brief 四个真实越限必须产生对应止挡穿透且被正常规则拒绝；不声称接触求解器自动阻挡。"""
    s=spec or load_spec();expected={(role,target) for role,b in s['travel_mm'].items() for target in (b[0]-2,b[-1]+2)}
    if len(rows)!=4 or {(r['request']['role'],r['request']['requested_mm']) for r in rows}!=expected:return False
    for row in rows:
        request=row['request'];role=request['role'];target=request['requested_mm'];snap=row['snapshot']
        if not request_valid(request) or not near(request['actual_transform'][11]*1000,target,.001) or row.get('restored') is not True:return False
        interference=snap['interference'];names={c['name']:c['role'] for c in snap['components']}
        stop=('support_left' if target<-58 else 'base') if role=='slider_left' else ('base' if target<4 else 'support_right')
        if interference.get('status')!='warn' or interference.get('errors')!=[] or (interference.get('interference_count') or 0)<=0:return False
        if not any(i.get('volume_mm3',0)>0 and {role,stop}.issubset({names[c['name']] for c in i['components']}) for i in interference['items']):return False
        poses=positions_for(target if role=='slider_left' else -40,target if role=='slider_right' else 22,s)
        validation=native_valid(snap,poses,s)
        failed={k for k,v in validation['checks'].items() if not v}
        if not failed or not failed.issubset({role+'_travel_limit_bounds','native_interference'}):return False
    return True


def extra_evidence():
    """@brief 保持、配合、九工况和全部原生零件均为必需证据。"""
    return {'spec.json','engineering_plan.json','parts.json','travel_report.json','tolerance_report.json','native_fault_controls.json','bom.csv',
        'assembly_progress.json',*(f'checkpoints/{stage}.SLDASM' for stage in ('components_inserted','before_travel_limits','mates_created')),
        *(f'parts/{name}.SLDPRT' for name in load_spec()['parts'])}


def workflow_checks(report):
    """@brief 每次从原始事实重算；只能接受预期人工 Review 阻塞。"""
    s=load_spec(); parts=report['parts']; checks={'eight_native_parts':set(parts)==set(s['parts'])}
    for name in s['parts']: checks['part_'+name]=all(validate_part(parts[name][stage],name,s)['status']=='pass' for stage in ('built','reopened'))
    for stage in ('built','reopened'):
        checks[stage]=native_valid(report[stage],spec=s)['status']=='pass' and neutral_valid(report[stage]['neutral'],s) and bom_valid(report[stage]['bom'],s)
    checks['identity_persisted']={c['role']:(c['id'],c['name'],c['path']) for c in report['built']['components']}=={c['role']:(c['id'],c['name'],c['path']) for c in report['reopened']['components']}
    checks['step']=neutral_valid(report['step_reopened'],s)
    checks['nine_travel_states']=travel_valid(report['travel_report'],s)
    checks['tolerance']=report['tolerance_report']==tolerance_contract(s) and all(near(a,b,1e-9) for a,b in zip(tolerance_contract(s)['diametral_clearance_limits_mm'],s['fit']['clearance_limits_mm']))
    controls=report['native_fault_controls']
    checks['raw_transform_request_keeps_valid_pose']=overtravel_valid(controls['overtravel'],s)
    checks['physical_overtravel_detected']=physical_overtravel_valid(controls['physical_overtravel'],s)
    collision=controls['collision']['snapshot']
    checks['real_collision_detected']=controls['collision']['restored'] is True and request_valid(controls['collision']['request']) and near(controls['collision']['request']['actual_transform'][11],-.003,1e-7) \
        and collision['interference']['status']=='warn' and (collision['interference']['interference_count'] or 0)>0 and not collision['interference']['errors'] \
        and any(i.get('volume_mm3',0)>0 and {'base','slider_left'}.issubset({c['role'] for c in collision['components'] if c['name'] in {x['name'] for x in i['components']}}) for i in collision['interference']['items'])
    suppressed=controls['suppressed_mate']['snapshot']
    checks['mate_suppression_detected']=controls['suppressed_mate']['restored'] is True and native_valid(suppressed,spec=s)['status']=='fail' \
        and any(m['name']=='slider_left_travel_limit' and m['suppressed'] is True for m in suppressed['mates']['mates'])
    review=report['review']; evaluation=review['evaluation']
    checks['review']=evaluation.get('status')=='pass' and evaluation.get('issues')==[] and all(review['checks'].get(k) is True for k in ('rebuild_succeeded','model_available','previews_created','previews_not_blank','expected_outputs_exist','feature_summary_available','feature_errors_absent'))
    checks['previews']=len(review['previews'])==4 and {p['path'] for p in review['previews']}=={str(Path(report['output_dir'])/'review'/f'{CASE_ID}_{v}.bmp') for v in VIEWS} and all(p['exists'] and not p['likely_blank'] for p in review['previews'])
    return checks


def inspect_delivery(output,report):
    """@brief 磁盘 BOM、独立规格及所有装配引用必须属于本轮并与真实哈希一致。"""
    s=load_spec()
    if json.loads((output/'spec.json').read_text(encoding='utf-8'))!=s: raise ValueError('SPEC_MISMATCH')
    with (output/'bom.csv').open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.DictReader(stream))
    expected=report['reopened']['bom']
    if len(rows)!=len(expected) or any(any(r.get(k)!=str(v) for k,v in e.items()) for r,e in zip(rows,expected)): raise ValueError('BOM_MISMATCH')
    for stage in ('built','reopened'):
        for c in report[stage]['components']:
            path=output/'parts'/f"{s['instances'][c['role']]['part']}.SLDPRT"
            if Path(c['path']).resolve()!=path.resolve() or not path.is_file() or sha256_file(path)!=c['file_sha256']: raise ValueError('REFERENCE_MISMATCH')


def create_parts(session,output,report,spec):
    """@brief 参数化实体和贯穿功能接口；铆钉为成形后双头实体，不含装饰螺纹。"""
    from scripts.sw_connect import get_com_member as member
    from scripts.sw_part import sketch,sketch_circle,sketch_rectangle,extrude_boss,extrude_cut
    from scripts.sw_sketch_quality import fully_define_sketch
    from scripts.sw_document_data import set_custom_properties
    from scripts.sw_appearance import set_document_appearance
    database=resolve_material_database(report['runtime_config'],report['doctor']['installations']['solidworks']['executable'])
    report['resolved_material_database']={'path':str(database),'sha256':sha256_file(database)}
    report['parts']={}; (output/'parts').mkdir()
    for name,p in spec['parts'].items():
        model=session.new_part(report['resolved_templates']['part']['path']); bosses=[]
        shapes=p.get('bosses',[[0,0,p.get('length',0),0,0,0]])
        if 'head_diameter' in p: shapes=[*shapes,[0,0,p['head_length'],0,0,-p['head_length']],[0,0,p['head_length'],0,0,p['length']]]
        for index,(x,y,depth,cx,cy,start) in enumerate(shapes):
            with sketch(model,'Front Plane') as ref:
                if 'diameter' in p: sketch_circle(model,0,0,(p['diameter'] if index==0 else p['head_diameter'])/2000)
                else: sketch_rectangle(model,cx/1000,cy/1000,x/1000,y/1000)
                fully_define_sketch(model)
            boss=extrude_boss(model,ref,depth/1000,direction=False,start_offset=start/1000)
            if boss is None: raise RuntimeError('凸台失败: '+name)
            boss.Name=name+'_boss_'+str(index); bosses.append(str(member(boss,'Name')))
        cuts=[]
        if 'guide' in p: cuts.append(('Front Plane',[[x,0,0] for x in spec['guide_centers_mm']],spec['guide_hole_diameter_mm']))
        if 'tie' in p: cuts.append(('Front Plane',[[x,y,0] for x,y in spec['tie_centers_mm']],spec['tie_hole_diameter_mm']))
        if 'finger_holes' in p: cuts.append(('Top Plane',[[x,0,z] for x,z in spec['finger_centers_mm']],spec['finger_hole_diameter_mm']))
        if p.get('mount'): cuts.append(('Top Plane',[[x,0,z] for x,z in spec['mount_centers_mm']],spec['mount_hole_diameter_mm']))
        for index,(plane,points,diameter) in enumerate(cuts):
            with sketch(model,plane) as ref:
                t=list(member(member(member(model.SketchManager,'ActiveSketch'),'ModelToSketchTransform'),'ArrayData'))
                for point in points:
                    local=world(point,t); sketch_circle(model,local[0]/1000,local[1]/1000,diameter/2000)
                fully_define_sketch(model)
            cut=extrude_cut(model,ref,0,direction=True,both_directions=plane=='Top Plane')
            if cut is None: raise RuntimeError('功能贯穿孔失败: '+name+' '+str(index))
            cut.Name=name+'_cut_'+str(index)
        member(model,'SetMaterialPropertyName2','',str(database),p['material'])
        result=set_custom_properties(model,{'PartNumber':'P021-'+name.upper(),'Description':description(name,spec),'Material':p['material']})
        if result['success'] is not True: raise RuntimeError('原生属性回读失败: '+name)
        color=[.3,.4,.6] if name in ('base','support','endplate') else [.8,.5,.15] if name=='slider' else [.7,.7,.72] if name in ('rod','tie_rivet','finger_rivet') else [.35,.35,.38]
        set_document_appearance(model,color)
        report['parts'][name]={}
        for stage in ('built','reopened'):
            if member(model,'ForceRebuild3',False) is not True: raise RuntimeError('零件重建失败')
            snap=snapshot_model(model,bosses[0]); snap['neutral']=previous.neutral_snapshot(model)
            snap['validation']=validate_part(snap,name,spec); report['parts'][name][stage]=snap
            write_json(output/'parts.json',report['parts'])
            if snap['validation']['status']!='pass':
                failed=output/'failures'/f'{name}_{stage}.SLDPRT';failed.parent.mkdir(exist_ok=True)
                if not failed.exists(): session.save(model=model,file_path=str(failed))
                raise RuntimeError('零件几何失败: '+name+' '+str(snap['validation']))
            if stage=='built':
                path=output/'parts'/f'{name}.SLDPRT'
                if path.exists() or not session.save(model=model,file_path=str(path)): raise RuntimeError('零件保存失败')
                session.close(model=model); model=session.open(str(path),silent=True)
        session.close(model=model); print('零件完成: '+name,flush=True)


def create_assembly(session,output,spec,template):
    """@brief 真实 Mate 定位与距离上下限；所有矩阵写入均逐项回读。"""
    from scripts.sw_connect import get_com_member as member,create_empty_dispatch_variant
    from scripts.sw_assembly import add_component,create_math_transform,get_component_feature_entity,get_assembly_entity,iter_feature_tree,select_entities_for_mate,add_mate5_checked
    model=session.new_assembly(template); components={}
    progress={'stage':'inserting_components','completed_roles':[],'completed_mates':[],'next_mate':None,'checkpoints':[]}
    def journal():
        """@brief 每次 COM 调用前记录下一步；不能将进度记录当作成功证据。"""
        write_json(output/'assembly_progress.json',progress)
    def checkpoint(stage):
        """@brief 独立原生副本保留装配现场；禁止覆盖已有 CAD，最终验收仍重新回读。"""
        path=output/'checkpoints'/f'{stage}.SLDASM';path.parent.mkdir(exist_ok=True)
        if path.exists() or not session.save(model=model,file_path=str(path)):raise RuntimeError('装配检查点保存失败: '+stage)
        progress['checkpoints'].append(str(path));progress['stage']=stage;journal()
    journal()
    for role,i in spec['instances'].items():
        c=add_component(model,str(output/'parts'/f"{i['part']}.SLDPRT"),sw=session.sw)
        if c is None: raise RuntimeError('组件插入失败: '+role)
        c.ComponentReference=role; model.ClearSelection2(True)
        if not c.Select4(False,create_empty_dispatch_variant(),False): raise RuntimeError('组件选择失败')
        member(model,'UnfixComponent'); desired=transform(i); c.Transform2=create_math_transform(session.sw,desired)
        actual=list(member(member(c,'Transform2'),'ArrayData'))
        if len(actual)!=16 or not all(near(a,b,1e-10) for a,b in zip(actual,desired)): raise RuntimeError('组件初始放置失败')
        if i['fixed']:
            model.ClearSelection2(True); c.Select4(False,create_empty_dispatch_variant(),False); member(model,'FixComponent')
        components[role]=c
        progress['completed_roles'].append(role);journal()
    checkpoint('components_inserted')
    def cylinder(role,x):
        c=components[role]; part=member(c,'GetModelDoc2'); faces=[]
        for body in member(part,'GetBodies2',0,False):
            for face in member(body,'GetFaces'):
                surface=member(face,'GetSurface')
                if member(surface,'IsCylinder'):
                    p=list(member(surface,'CylinderParams'))
                    if near(p[0]*1000,x,.001) and near(abs(p[5]),1,1e-6) and near(p[6]*2000,spec['guide_hole_diameter_mm'] if 'guide' in spec['parts'][spec['instances'][role]['part']] else spec['parts']['rod']['diameter'],.001): faces.append(face)
        if len(faces)!=1: raise RuntimeError('导向圆柱面不唯一: '+role)
        return get_assembly_entity(c,faces[0])
    for wanted in mate_contract(spec):
        progress['stage']='creating_mates';progress['next_mate']=wanted['name'];journal()
        if 'minimum_mm' in wanted and len(progress['checkpoints'])==1:checkpoint('before_travel_limits')
        a,b=wanted['roles']
        entities=[cylinder(a,wanted['left_x']),cylinder(b,wanted['right_x'])] if wanted['type']==1 else [get_component_feature_entity(components[r],plane) for r,plane in zip((a,b),wanted['planes'])]
        before={member(f,'Name') for f,_ in iter_feature_tree(model)}
        select_entities_for_mate(model,*entities)
        mate=add_mate5_checked(model,wanted['type'],align=2 if spec['instances'][a].get('angle_x') else 0,
            distance=wanted.get('distance_mm',0)/1000,flip=wanted.get('flip',False),lock_rotation=False,
            distance_upper=wanted.get('maximum_mm',wanted.get('distance_mm',0))/1000,
            distance_lower=wanted.get('minimum_mm',wanted.get('distance_mm',0))/1000)
        if mate is None: raise RuntimeError('Mate 返回空')
        added=[f for f,_ in iter_feature_tree(model) if member(f,'Name') not in before and str(member(f,'GetTypeName2')).startswith('Mate') and member(f,'GetTypeName2')!='MateGroup']
        if len(added)!=1: raise RuntimeError('Mate 特征无法唯一关联')
        added[0].Name=wanted['name']; print('Mate: '+wanted['name'],flush=True)
        progress['completed_mates'].append(wanted['name']);journal()
    progress['next_mate']=None;checkpoint('mates_created')
    return model,components


def native_snapshot(model,light=False,contacts=False):
    """@brief 真实依赖、组件、Mate 和干涉回读；工况轻量模式不省略碰撞计算。"""
    from scripts.sw_connect import get_com_member as member
    from scripts.sw_assembly import inspect_native_mates,iter_feature_tree
    from scripts.sw_measure import inspect_interference
    if light:
        rows=[]
        for c in member(model,'GetComponents',True) or []:
            path=Path(member(c,'GetPathName'))
            rows.append({'role':str(member(c,'ComponentReference')),'id':int(member(c,'GetID')),'name':str(member(c,'Name2')),
                'path':str(path),'file_exists':path.is_file(),'fixed':bool(member(c,'IsFixed')),'suppression':int(member(c,'GetSuppression2')),
                'constrained_status':int(member(c,'GetConstrainedStatus')),'transform':list(member(member(c,'Transform2'),'ArrayData'))})
        deps=list(member(model,'GetDependencies2',True,True,False) or [])
        result={'components':rows,'raw_dependencies':deps,'reference_files':sorted(set(str(Path(p)) for p in deps if Path(str(p)).is_absolute() and str(p).lower().endswith('.sldprt'))),
            'reference_errors':[],'mates':inspect_native_mates(model),'interference':inspect_interference(model)}
    else: result=previous.assembly_snapshot(model)
    result['rebuild']=bool(member(model,'ForceRebuild3',False)); result['limits']={}
    for feature,_ in iter_feature_tree(model):
        if str(member(feature,'Name')).endswith('_travel_limit'):
            data=member(feature,'GetDefinition')
            result['limits'][str(member(feature,'Name'))]={'advanced':bool(member(data,'IsAdvancedMate')),'distance_mm':float(member(data,'Distance'))*1000,'minimum_mm':float(member(data,'MinimumDistance'))*1000,'maximum_mm':float(member(data,'MaximumDistance'))*1000}
    if contacts: result['coincidence']=inspect_interference(model,treat_coincidence_as_interference=True)
    return result


def move_slider(model,components,sw,role,z,persist_mate=False,inject_fault=False):
    """@brief 真实求解请求及限位 Mate 当前值持久化；所有工况仍验证重建后的实际位姿。

    @details 修改 Distance 可自动扩界，正常入口只接受固化规格内工况；故障入口显式保留真实扩界和穿透证据。
    不把此静态工况控制宣称为自由拖动或接触动力学，不直接写 Transform2 模拟配合。
    """
    from scripts.sw_connect import get_com_member as member,create_empty_dispatch_variant
    from scripts.sw_assembly import create_math_transform,iter_feature_tree
    spec=load_spec();low,mid,high=spec['travel_mm'][role]
    if persist_mate and not inject_fault and not low<=z<=high:raise ValueError('正常工况禁止越出固化行程')
    c=components[role]; target=list(member(member(c,'Transform2'),'ArrayData')); target[11]=z/1000
    t=create_math_transform(sw,target); readback=list(member(t,'ArrayData'))
    success=bool(member(c,'SetTransformAndSolve2',t));update={}
    if persist_mate:
        wanted=next(m for m in mate_contract(spec) if m['name']==role+'_travel_limit')
        feature=next(f for f,_ in iter_feature_tree(model) if member(f,'Name')==wanted['name'])
        reference=float(member(member(components[wanted['roles'][1]],'Transform2'),'ArrayData')[11])*1000
        data=member(feature,'GetDefinition');data.Distance=(z-reference)/1000
        data.MinimumDistance=wanted['minimum_mm']/1000;data.MaximumDistance=wanted['maximum_mm']/1000;data.IsAdvancedMate=True
        modified=bool(member(feature,'ModifyDefinition',data,model,create_empty_dispatch_variant()))
        current=member(feature,'GetDefinition')
        update={'modified':modified,'reference_origin_mm':reference,'advanced':bool(member(current,'IsAdvancedMate')),
            'distance_mm':float(member(current,'Distance'))*1000,'minimum_mm':float(member(current,'MinimumDistance'))*1000,'maximum_mm':float(member(current,'MaximumDistance'))*1000}
        success &= modified
    rebuild=bool(member(model,'ForceRebuild3',False))
    return {'role':role,'requested_mm':z,'requested_transform':target,'request_readback':readback,
        'positioning_method':'native_limit_mate_current_distance' if persist_mate else 'native_transform_request_no_parameter_commit',
        'mate_update':update,'solver_success':success and rebuild,'actual_transform':list(member(member(c,'Transform2'),'ArrayData'))}


def travel_checks(model,components,sw,spec):
    """@brief 九个真实组合；每个工况独立重建、读配合、计算穿透及有意接触。"""
    rows=[]
    for left,right in itertools.product(spec['travel_mm']['slider_left'],spec['travel_mm']['slider_right']):
        requests=[move_slider(model,components,sw,'slider_left',left,True),move_slider(model,components,sw,'slider_right',right,True)]
        row={'left_mm':left,'right_mm':right,'requests':requests,'solver_success':all(r['solver_success'] for r in requests),'snapshot':native_snapshot(model,light=True,contacts=True)}
        rows.append(row); print(f'行程工况: left={left}, right={right}, 原生穿透={row["snapshot"]["interference"]["interference_count"]}',flush=True)
    move_slider(model,components,sw,'slider_left',-40,True); move_slider(model,components,sw,'slider_right',22,True)
    return rows


def fault_controls(model,components,sw,spec):
    """@brief 真实越限请求、Mate 抑制和穿透控制；只在自有文档操作并恢复。"""
    import pythoncom
    from win32com.client import VARIANT
    from scripts.sw_connect import get_com_member as member
    from scripts.sw_assembly import iter_feature_tree
    result={'overtravel':[],'physical_overtravel':[]}
    for role,bounds in spec['travel_mm'].items():
        for target in (bounds[0]-2,bounds[-1]+2):
            row=move_slider(model,components,sw,role,target); row['actual_mm']=row['actual_transform'][11]*1000
            row['within_bounds']=bounds[0]-.001<=row['actual_mm']<=bounds[-1]+.001
            row['snapshot']=native_snapshot(model,light=True)
            result['overtravel'].append(row); move_slider(model,components,sw,role,bounds[1],True)
            request=move_slider(model,components,sw,role,target,True,True)
            physical={'request':request,'snapshot':native_snapshot(model,light=True)}
            restored=move_slider(model,components,sw,role,bounds[1],True)
            physical['restored']=restored['solver_success'] and near(restored['actual_transform'][11]*1000,bounds[1],.001)
            result['physical_overtravel'].append(physical)
    feature=next(f for f,_ in iter_feature_tree(model) if member(f,'Name')=='slider_left_travel_limit')
    empty=VARIANT(pythoncom.VT_EMPTY,None)
    if not feature.SetSuppression2(0,1,empty): raise RuntimeError('限位 Mate 抑制失败')
    try:
        member(model,'ForceRebuild3',False); suppressed=native_snapshot(model,light=True)
    finally:
        if not feature.SetSuppression2(1,1,empty): raise RuntimeError('限位 Mate 恢复失败')
        member(model,'ForceRebuild3',False)
    request=move_slider(model,components,sw,'slider_left',-3,True,True)
    collision={'request':request,'snapshot':native_snapshot(model,light=True)}
    restore=move_slider(model,components,sw,'slider_left',-40,True)
    collision['restored']=restore['solver_success'] and near(restore['actual_transform'][11],-.04,1e-7)
    result.update(collision=collision,suppressed_mate={'snapshot':suppressed,'restored':member(feature,'IsSuppressed') is False})
    return result


def run_cad(output,report):
    """@brief 复用 Session 和既有安全清理，生命周期不改变 Core 只适配终态的事实。"""
    import pythoncom
    from scripts.sw_session import SolidWorksSession
    from scripts.sw_connect import get_com_member as member,get_sw_version,find_template
    from scripts.sw_process import solidworks_processes
    from scripts.sw_part import clear_sketch_selection_cache
    from scripts.sw_review import run_review
    from scripts.sw_delivery import export_assembly_bom_csv
    from apps.desktop.cad_workbench.engineering_orchestrator import build_engineering_plan
    s=load_spec(); write_json(output/'spec.json',s)
    plan=build_engineering_plan('夹爪机械保持、公差及九组合行程；原生装配、BOM、STEP；排除驱动、同步、Motion 和载荷').to_dict()
    plan['execution_scope']={'core_scope':'terminal_adaptation_only','manufacturing_release':False,
        'implemented':['native_parts','native_mates','nine_static_states','BOM','STEP','review'],
        'excluded':['自由拖动认证','接触动力学','同步运动','驱动','载荷','Motion Study']}; write_json(output/'engineering_plan.json',plan)
    prior=solidworks_processes(); report['processes_before']=prior
    if prior is None or prior: raise RuntimeError('BLOCKED: 已有或未知 SolidWorks 进程，保留用户实例')
    session=None; model=None; components={}; pid=None; owned=False; pythoncom.CoInitialize()
    try:
        session=SolidWorksSession(version=2026,wait_seconds=report['runtime_config']['connect_timeout_seconds'],visible=True,max_documents=20)
        report.update(connection=session.connection_info,version=get_sw_version(session.sw))
        pid=int(member(session.sw,'GetProcessID')); report['process_created_at']=process_created_at(pid)
        owned=session.connection_info.get('started_by_cad_studio') is True and pid not in prior
        if not owned or report['version']['year']!=2026 or list(member(session.sw,'GetDocuments') or []): raise RuntimeError('BLOCKED: 无法证明独占实例')
        report['resolved_templates']={}
        for kind in ('part','assembly'):
            path=report['runtime_config'][kind+'_template'] or find_template(session.sw,kind)
            if not path or not Path(path).is_file(): raise RuntimeError('BLOCKED: 缺少原生模板')
            report['resolved_templates'][kind]={'path':str(path),'sha256':sha256_file(Path(path))}
        create_parts(session,output,report,s)
        model,components=create_assembly(session,output,s,report['resolved_templates']['assembly']['path'])
        report['travel_report']=travel_checks(model,components,session.sw,s); write_json(output/'travel_report.json',report['travel_report'])
        report['native_fault_controls']=fault_controls(model,components,session.sw,s); write_json(output/'native_fault_controls.json',report['native_fault_controls'])
        report['built']=native_snapshot(model); write_json(output/'built.json',report['built'])
        report['tolerance_report']=tolerance_contract(s); write_json(output/'tolerance_report.json',report['tolerance_report'])
        path=output/f'{CASE_ID}.SLDASM'
        if path.exists() or not session.save(model=model,file_path=str(path)): raise RuntimeError('装配保存失败')
        previous.close_test_documents(session,output); model=None; components={}; gc.collect()
        model=session.open(str(path),silent=True); report['reopened']=native_snapshot(model); write_json(output/'reopened.json',report['reopened'])
        export_assembly_bom_csv(model,output/'bom.csv'); step=output/f'{CASE_ID}.step'
        if step.exists() or not session.export(model=model,output_path=str(step)): raise RuntimeError('STEP 导出失败')
        if not step.read_text(encoding='ascii',errors='replace').lstrip().startswith('ISO-10303-21;'): raise RuntimeError('STEP 文件头无效')
        report['review'],report['review_path']=run_review(model,output/'review',basename=CASE_ID,views=VIEWS,expected_outputs=[str(path),str(step),str(output/'bom.csv')])
        previous.close_test_documents(session,output); model=None; gc.collect()
        model=session.open(str(step),silent=True); report['step_reopened']=previous.neutral_snapshot(model); write_json(output/'step_reopened.json',report['step_reopened'])
    finally:
        cleanup={'status':'failed','owned_pid':pid,'errors':[]}; report['cleanup']=cleanup
        try:
            if session is not None and owned:
                previous.close_test_documents(session,output); cleanup['remaining_documents']=len(list(member(session.sw,'GetDocuments') or []))
                if cleanup['remaining_documents']==0: cleanup['exit_requested']=session.quit_owned_instance()
        except Exception: cleanup['errors'].append(traceback.format_exc())
        model=None; components={}; session=None; clear_sketch_selection_cache(); gc.collect(); pythoncom.CoUninitialize()
        deadline=time.monotonic()+report['runtime_config']['cleanup_timeout_seconds']
        while pid and time.monotonic()<deadline:
            after=solidworks_processes()
            if after is None or pid not in after: break
            time.sleep(.25)
        cleanup['processes_after']=solidworks_processes()
        if cleanup.get('remaining_documents')==0 and cleanup.get('exit_requested') is True and cleanup['processes_after']==prior and not cleanup['errors']: cleanup['status']='pass'


def main(argv=None):
    """@brief 进入已验证的双状态/证据/五轮/退出码共用壳。"""
    return harness.main(sys.modules[__name__],argv)


if __name__=='__main__':
    raise SystemExit(main())
