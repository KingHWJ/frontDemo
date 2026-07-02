# 查询实时，获取文件
select t2.project_name, t2.project_alias, t2.status, t2.is_deleted, t1.*
from streamapp.rdos_stream_catalogue t1
         left join streamapp.rdos_project t2 on t1.project_id = t2.id
where node_name = '任务开发'
  and level = 1
  and t2.status = 1
  and t2.is_deleted = 0
;

# 查询离线，获取文件
select t2.project_name, t2.project_alias, t2.status, t2.is_deleted, t1.*
from ide.rdos_batch_catalogue t1
         left join ide.rdos_project t2 on t1.project_id = t2.id
where node_name = '任务开发'
  and level = 1
  and t2.status = 1
  and t2.is_deleted = 0
;

# 查询实时，任务列表
select * from streamapp.rdos_stream_task;
select * from streamapp.rdos_stream_task_operation_log;
select * from ide.rdos_batch_task_version;


select * from streamapp.rdos_stream_task where task_id =
'4uav9fo0a930';

# 查询实时提交保存版本记录
select * from streamapp.rdos_stream_task_version where task_id = 49;

# 查询离线，任务列表
select * from ide.rdos_batch_task where name = 'test11111hwj';

select * from ide.rdos_batch_task where id = 19415;

# 版本记录表
select * from ide.rdos_batch_task_version where id = 145649 ;

# 获取当前的task依赖的任务id
select task_id,parent_task_id from rdos_batch_task_task where task_id = 17403 ;


# 参数校验,实时任务创建删除时，node_id一定要是project_id下面
select id from streamapp.rdos_stream_catalogue where project_id = 19;


select * from ide.rdos_batch_catalogue where project_id = 21;

select * from dt_pub_service.dsc_info where is_meta = 0 and is_deleted = 0;


# 项目空间

# 测试环境任务提交，合并到main分支，git拉取，确认ads_temp_1任务产生了变更
# 新增任务 ads_temp_1
# 1.查询测试元数据库，获取ads_temp_1的基本属性，任务id，项目空间id,所属目录id，目录名,用户等信息
# 2.查询生产元数据库，获取项目空间映射的生产项目空间id，获取目录名是否存在，存在关系是否和测试一致，如果缺少，则需要创建同名目录；一致则下一步
# 3.去执行，新增任务的接口，当前已获取到生产的项目id,目录id,任务名等，调用接口，用户名等，信息，然后提交
# 4.生产环境应该就能创建成功，基于已有的信息，更新代码等文件，保存
# 5.提交发布接口

# 更新任务 ads_temp_2
# 1.查询测试元数据库，获取ads_temp_1的基本属性，任务id，项目空间id,所属目录id，目录名,用户等信息
# 2.查询生产元数据库，获取项目空间映射的生产项目空间id，查询任务名是否存在，存在，获取到任务的元数据信息
# 3.调用元数据库的基本信息，已存在的任务，只修改代码文件，不修改环境参数，保存
# 4.提交发布接口



