# 文档

## 前置步骤

1. 将dist目录下`processor.worker.js`复制到项目目录中，使其可以通过浏览器地址栏访问，

## AudioPlayer

浏览器播放音频管理

## 方法

### AudioPlayer.constructor(processorPath)

构造函数

> `processorPath`是 `processor.worker.js`的路径地址，如果访问地址`/a/b/c/processor.worker.js`，则processorPath为`/a/b/c`

### AudioPlayer.start(Object object)

开始：准备音频播放环境、清空音频数据、并准备接收音频数据

Object object

| 属性 | 类型 | 默认值 | 必填 | 取值范围 | 说明 |
| ------ | ------ | ------ | ------ | ------ | ------ |
| sampleRate | number | 16000 | 否 | - | 音频数据采样率 |
| autoPlay | boolean |  true  | 否 | - | 是否自动播放 |
| resumePlayDuration | number | 1000 | false | - | 已经接收到的音频数据播放完，后续需要播放的数据还没有来到，需要延时多长时间继续播放，单位为`ms`, `<=0` 时不自动播放后续的音频数据 |

### AudioPlayer.postMessage(Object object)

保存音频数据

Object object

| 属性 | 类型 | 默认值 | 必填 | 取值范围 | 说明 |
| ------ | ------ | ------ | ------ | ------ | ------ |
| type | string | - | 是 | base64、string、Int16Array、Float32Array | 音频数据类型 |
| data | string、Int16Array、Float32Array |  -  | 是 | - | 音频数据 |
| isLastData | boolean |  -  | 是 | false、true | 是否为最后的音频数据 |

### AudioPlayer.play

播放音频

### AudioPlayer.reset

重置音频环境，

> 停止音频播放、清空音频数据、且不再接收`postMessage`传递过来的数据，
将在调用start后才会继续接收

### AudioPlayer.stop

停止播放
> 停止音频播放、保留音频数据、继续接收`postMessage`传递过来的数据

### AudioPlayer.getAudioDataBlob(type)

获取音频blob数据

> 将音频数据转成blob对象

| 属性 | 类型 | 默认值 | 必填 | 取值范围 | 说明 |
| ------ | ------ | ------ | ------ | ------ | ------ |
| type | string | pcm | 否 | "pcm"、"wav" | blob的类型 |

### AudioPlayer.onPlay = function listener

开始播放回调事件

### AudioPlayer.onStop = function listener

停止播放回调事件

```js
const listener = (audioDatas: Float32Array[]) => {}
```

| 属性 | 类型 | 说明 |
| ------ | ------ | ------ |
| audioDatas | Float32Array[] | 音频数据数组 |

## 示例代码

```js
  const audioPlayer = new AudioPlayer("../../dist");

  audioPlayer.onPlay = () => {
    console.log("play")
  };
  audioPlayer.onStop = (audioDatas) => {
    console.log(audioDatas);
  };
  audioPlayer.start({
    autoPlay: true,
    sampleRate: 16000,
  });

  audioPlayer.postMessage({
    type: "base64",
    data: audioData,
  });

  audioPlayer.stop();

```

## 合成示例

查看example/tts
将example/tts/index.js下面的值替换成真实的值

```js
  const APPID = "xxx";
  const API_SECRET = "xxx";
  const API_KEY = "xxx";
```

### 运行

1. 安装 `npm install -g http-server`
2. 运行 `http-server .`
3. 浏览器打开听写示例：http://127.0.0.1:xxx/example/tts/index.html# tts
