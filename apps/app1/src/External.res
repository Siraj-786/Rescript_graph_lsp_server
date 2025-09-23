// src/External.res

let logCurrentTime = () => {
  // Js.log is a built-in function that maps to console.log
  Js.log("Logging the current time:")

  // Js.Date.now() is a built-in function
  let now = Js.Date.now()

  Js.log(now)
}

let isToday = (date: Js.Date.t): bool => {
  // Js.Date.getDay() is another built-in
  Js.Date.getDay(date) == Js.Date.getDay(Js.Date.fromFloat(Js.Date.now()))
}