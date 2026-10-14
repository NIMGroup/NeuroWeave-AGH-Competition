import { ui } from './libraryText';
const states:Record<string,string>={published:'已发布',archived:'已归档',passed:'已通过',review:'需审阅',error:'错误',not_checked:'未检查',deferred:'待上游生成',queued:'排队中',running:'运行中',cancel_requested:'请求取消',cancelled:'已取消',succeeded:'执行成功',failed:'失败',chain_ready:'待确定流程',awaiting_method_choice:'待选择方法',awaiting_input_binding:'待绑定输入',awaiting_parameter_review:'待审阅参数',awaiting_dry_run:'待检查',ready:'待确认',confirmed:'已确认'};
export const stateText=(value:string)=>ui(states[value]??value);
