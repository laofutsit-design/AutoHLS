const findDelivery = id => document.querySelector(id);
let deliveryRequest = 0;

async function loadDelivery() {
  const request = ++deliveryRequest;
  findDelivery('#deliveryContent').hidden = true;
  findDelivery('#deliveryStatus').textContent = '正在校验本地交付文件…';
  try {
    const response = await fetch('/api/delivery', {cache: 'no-store'});
    if (!response.ok) throw new Error('交付证据缺失或校验失败；不展示通过状态。');
    const data = await response.json();
    if (request !== deliveryRequest) return;
    if (!data.available) {
      findDelivery('#deliveryStatus').textContent = '尚无完成准备的交付任务。不使用演示数据填补。';
      return;
    }
    if (data.mode !== 'read_only_delivery' || typeof data.board_verified !== 'boolean') {
      throw new Error('交付记录类型不符合此页面的验收范围。');
    }
    const boardVerified = data.board_verified === true && data.state === 'board_functional_verified' &&
      data.board?.complete === true && data.board.board_verified === true && data.board.steps === 14 &&
      data.board.bit_sha256 === data.receipt?.bit_sha256 &&
      data.board.receipt_sha256 === data.receipt_sha256 && data.board.cpu_speedup === null;
    if ((data.board_verified || data.state === 'board_functional_verified') && !boardVerified) {
      throw new Error('实板验收记录缺失或与本次固件不匹配。');
    }
    const verified = (data.state === 'awaiting_board_confirmation' || boardVerified) && data.receipt?.board_verified === false;
    if (boardVerified && !verified) throw new Error('离线固件凭据不得被改写为板测结果。');
    findDelivery('#deliveryStatus').textContent = boardVerified
      ? '交付链与实板功能验收通过：14 / 14 项。此结果不代表新增性能优势。'
      : verified
      ? '离线固件验收通过；等待本次板卡安全确认与独立实测。'
      : '搜索候选与构建输入已校验；尚无经过本地复核的完整固件交付包。';
    const stages = ['搜索及独立 RTL：原批次冻结引擎重审通过',
      `候选与构建输入：已冻结（${data.version}）`,
      verified ? '原生 / 包装 RTL / 布线：交付包离线复核通过' : '原生 / 包装 RTL / 布线：等待完整证据',
      boardVerified ? '实际下载与板测：14 项通过，原始结果已独立离线复核'
        : '实际下载与板测：未验收；历史性能不自动继承'];
    const list = findDelivery('#deliveryStages');
    list.replaceChildren();
    for (const text of stages) {
      const item = document.createElement('li');
      item.textContent = text;
      list.append(item);
    }
    findDelivery('#deliveryIdentity').textContent = `${data.job} → ${data.candidate} · ${data.contract}`;
    findDelivery('#deliveryMetric').textContent = `搜索阶段 HLS 时延估计：${data.search_metrics.latency_us} μs；不是包装后性能或板级时延。`;
    findDelivery('#deliveryHash').textContent = `候选 SHA-256：${data.source_sha256}`;
    findDelivery('#firmwareHash').textContent = verified ? `bit SHA-256：${data.receipt.bit_sha256}` : 'bit SHA-256：尚未取得验收包';
    findDelivery('#boardAcceptance').hidden = !boardVerified;
    if (boardVerified) {
      const board = data.board;
      findDelivery('#boardChecks').textContent = `输出比较 ${board.output_checks} 次 · 输入完整性 ${board.input_checks} 次 · 保护区 ${board.guard_checks} 次；共 ${board.output_checks + board.input_checks + board.guard_checks} 次，全部通过。`;
      findDelivery('#boardReceipt').textContent = `本次凭据 SHA-256：${board.receipt_sha256}`;
      findDelivery('#boardArchive').textContent = `板测证据包 SHA-256：${board.archive_sha256}`;
      findDelivery('#boardEndState').textContent = `采集结束状态：${board.final_idle ? '空闲' : '未知'}，待处理缓冲区 ${board.retained_buffers}，时钟 ${board.clock_mhz} MHz；这是归档状态，不是实时监控。`;
    }
    findDelivery('#deliverySource').textContent = data.source;
    findDelivery('#observationTime').textContent = data.observation ? `快照时间（UTC）：${data.observation.observed_utc}` : '尚无远程进度快照。';
    findDelivery('#deliveryObservation').textContent = data.observation ? JSON.stringify(data.observation, null, 2) : '未测量';
    findDelivery('#deliveryContent').hidden = false;
  } catch (error) {
    if (request !== deliveryRequest) return;
    findDelivery('#deliveryContent').hidden = true;
    findDelivery('#deliveryStatus').textContent = error.message;
  }
}
findDelivery('#refreshDelivery').addEventListener('click', loadDelivery);
loadDelivery();
